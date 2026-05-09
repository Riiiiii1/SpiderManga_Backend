import requests
import json
import time
from bs4 import BeautifulSoup

CDN = "https://cdn1.intomanga.com/i/m"
BASE_URL = "https://inmanga.com"


# ──────────────────────────────────────────
# SCRAPING
# ──────────────────────────────────────────

def buscar_manga(nombre: str) -> dict | None:
    """Busca un manga por nombre en InManga. Devuelve el primer resultado o None."""
    try:
        response = _get_con_retry(
            f"{BASE_URL}/manga/GetQuickSearch",
            params={"name": nombre}
        )
        response.raise_for_status()
        data = json.loads(response.json().get("data", "{}"))
        resultados = data.get("result", [])
        return resultados[0] if resultados else None
    except Exception as e:
        print(f"❌ Error buscando '{nombre}': {e}")
        return None


def obtener_capitulos(manga_uuid: str) -> list[dict]:
    """Devuelve la lista de capítulos de un manga ordenados por número."""
    try:
        response = _get_con_retry(
            f"{BASE_URL}/chapter/getall",
            params={"mangaIdentification": manga_uuid}
        )
        response.raise_for_status()
        data = json.loads(response.json().get("data", "{}"))
        resultados = data.get("result", [])
        return sorted(resultados, key=lambda x: float(x.get("Number", 0)))
    except Exception as e:
        print(f"❌ Error obteniendo capítulos de {manga_uuid}: {e}")
        return []


def obtener_paginas(capitulo_uuid: str) -> list[dict]:
    """Devuelve la lista de páginas de un capítulo con su UUID."""
    try:
        response = _get_con_retry(
            f"{BASE_URL}/chapter/chapterIndexControls",
            params={"identification": capitulo_uuid}
        )
        soup = BeautifulSoup(response.text, "html.parser")
        page_select = soup.find("select", {"id": "PageList"})

        if not page_select:
            return []

        return [
            {"numero": opt.text.strip(), "uuid": opt.get("value")}
            for opt in page_select.find_all("option")
        ]
    except Exception as e:
        print(f"❌ Error obteniendo páginas de {capitulo_uuid}: {e}")
        return []


def generar_urls_capitulo(manga_uuid: str, capitulo_uuid: str) -> list[str]:
    """Genera las URLs CDN de todas las páginas de un capítulo."""
    paginas = obtener_paginas(capitulo_uuid)
    return [
        f"{CDN}/{manga_uuid}/c/{capitulo_uuid}/o/{p['uuid']}.jpg"
        for p in paginas
    ]


# ──────────────────────────────────────────
# SPIDER COMPLETO — para cron job o seed inicial
# ──────────────────────────────────────────

def ejecutar_spider(lista_mangas: list[str], db_conn, solo_ultimos: int = None):
    """
    Raspa y guarda en BD los mangas indicados.

    Parámetros:
        lista_mangas  → lista de títulos a buscar
        db_conn       → conexión psycopg2 abierta (la provee quien llama)
        solo_ultimos  → si se pasa un número, solo procesa los últimos N capítulos
                        (útil para el cron job de actualización)
    """
    cursor = db_conn.cursor()

    for titulo in lista_mangas:
        print(f"\n🔍 Procesando: {titulo}")
        manga = buscar_manga(titulo)

        if not manga:
            print(f"  ⚠️  No encontrado en InManga.")
            continue

        manga_uuid = manga["Identification"]
        nombre     = manga["Name"]
        portada    = manga["ThumbnailPath"]
        sinopsis   = manga.get("Sinopsis", "")
        estado     = manga.get("BroadcastStatusDescription", "")

        # UPSERT manga
        cursor.execute("""
            INSERT INTO mangas (id_inmanga, nombre, portada_url, sinopsis, estado, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id_inmanga) DO UPDATE SET
                nombre     = EXCLUDED.nombre,
                portada_url= EXCLUDED.portada_url,
                sinopsis   = EXCLUDED.sinopsis,
                estado     = EXCLUDED.estado,
                updated_at = CURRENT_TIMESTAMP;
        """, (manga_uuid, nombre, portada, sinopsis, estado))

        print(f"  ✅ Manga '{nombre}' guardado.")

        # Capítulos
        capitulos = obtener_capitulos(manga_uuid)
        a_procesar = capitulos[-solo_ultimos:] if solo_ultimos else capitulos

        for cap in a_procesar:
            cap_uuid = cap["Identification"]
            numero   = float(cap["Number"])
            urls     = generar_urls_capitulo(manga_uuid, cap_uuid)

            cursor.execute("""
                INSERT INTO capitulos (id_inmanga, manga_id, numero, urls_imagenes, updated_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (id_inmanga) DO UPDATE SET
                    urls_imagenes = EXCLUDED.urls_imagenes,
                    updated_at    = CURRENT_TIMESTAMP;
            """, (cap_uuid, manga_uuid, numero, json.dumps(urls)))

            print(f"    -> Cap {numero:>6} — {len(urls)} páginas")
            time.sleep(0.8)  # Respetar el servidor

    db_conn.commit()
    cursor.close()
    print("\n🕸️  Spider finalizado.")


def buscar_manga_multiples(nombre: str) -> list[dict]:
    """
    Busca un manga por nombre en InManga.
    Devuelve TODOS los resultados con solo metadatos (sin capítulos).
    Ideal para el input de búsqueda del frontend.
    """
    try:
        response = _get_con_retry(
            f"{BASE_URL}/manga/GetQuickSearch",
            params={"name": nombre}
        )
        response.raise_for_status()
        data = json.loads(response.json().get("data", "{}"))
        resultados = data.get("result", [])

        return [
            {
                "id_inmanga":  r.get("Identification"),
                "nombre":      r.get("Name"),
                "portada_url": r.get("ThumbnailPath"),
                "sinopsis":    r.get("Sinopsis", ""),
                "estado":      r.get("BroadcastStatusDescription", ""),
            }
            for r in resultados
        ]
    except Exception as e:
        print(f"❌ Error en búsqueda múltiple '{nombre}': {e}")
        return []
    



def _get_con_retry(url: str, params: dict, intentos: int = 3) -> requests.Response:
    """Reintenta una petición hasta N veces si falla."""
    for intento in range(1, intentos + 1):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response
        except Exception as e:
            print(f"  ⚠️ Intento {intento}/{intentos} fallido: {e}")
            if intento < intentos:
                time.sleep(2 * intento)  # espera 2s, 4s entre reintentos
    raise Exception(f"❌ No se pudo conectar a {url} tras {intentos} intentos")