import json
import os
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from sse_starlette.sse import EventSourceResponse
from psycopg2.extras import RealDictCursor
import re
from fastapi.responses import JSONResponse
from fastapi import Request
from database import (
    get_db,
    db_listar_mangas,
    db_buscar_manga_por_nombre,
    db_obtener_manga_por_id,
    db_listar_capitulos,
    db_obtener_capitulo,
    db_upsert_manga,
    db_upsert_capitulo,
)
from models import (
    ListaMangasResponse,
    ListaCapitulosResponse,
    PaginasCapituloResponse,
    BusquedaResponse,
    ScrapeRequest,
    MangaDetalle,
)
from spider import (
    buscar_manga_multiples,
    ejecutar_spider,
    buscar_manga,
    obtener_capitulos,
    generar_urls_capitulo,
)

app = FastAPI(
    title="SpiderManga API",
    description="API para consumir mangas scrapeados de InManga",
    version="1.0.0"
)

@app.exception_handler(Exception)
async def error_global(request: Request, exc: Exception):
    print(f"❌ Error no manejado: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor. Intenta de nuevo."}
    )



UUID_REGEX = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)

def validar_uuid(manga_id: str):
    if not UUID_REGEX.match(manga_id):
        raise HTTPException(status_code=400, detail="ID de manga inválido")
    



# ──────────────────────────────────────────
# BACKGROUND TASK
# ──────────────────────────────────────────

def _scrape_y_guarda(nombres: list[str]):
    """Corre el spider en background. Abre su propia conexión."""
    with get_db() as conn:
        ejecutar_spider(nombres, conn)


# ──────────────────────────────────────────
# HEALTH
# ──────────────────────────────────────────

@app.get("/")
def health_check():
    return {"estado": "OK", "mensaje": "SpiderManga API en línea 🕷️"}


# ──────────────────────────────────────────
# MANGAS
# ──────────────────────────────────────────

# ──────────────────────────────────────────
# BÚSQUEDA RÁPIDA
# ──────────────────────────────────────────

@app.get("/buscar/{nombre}")
def buscar_rapido(nombre: str):
    """
    Búsqueda rápida — solo portadas y metadatos.
    No toca BD ni capítulos. Responde en ~1 segundo.
    Úsalo para el input de búsqueda del frontend.
    """
    resultados = buscar_manga_multiples(nombre)
    if not resultados:
        raise HTTPException(status_code=404, detail="No se encontraron mangas")
    return {"total": len(resultados), "resultados": resultados}


@app.get("/mangas", response_model=ListaMangasResponse)
def listar_mangas():
    """Lista todos los mangas guardados en la BD."""
    mangas = db_listar_mangas()
    return {"total": len(mangas), "resultados": mangas}


@app.get("/mangas/buscar/{nombre}", response_model=BusquedaResponse)
def buscar_o_scrape(nombre: str, background_tasks: BackgroundTasks):
    """
    Patrón cache-first:
    - Si el manga está en BD → lo devuelve al instante.
    - Si no está → lanza el spider en background y avisa al cliente.
    """
    manga = db_buscar_manga_por_nombre(nombre)

    if manga:
        return {"source": "cache", "data": MangaDetalle(**manga)}

    background_tasks.add_task(_scrape_y_guarda, [nombre])

    return {
        "source": "scraping",
        "mensaje": f"'{nombre}' no estaba en caché. Scraping iniciado, vuelve a consultar en unos segundos."
    }



# El endpoint stream actualizado
@app.get("/mangas/stream/{nombre}")
async def buscar_stream(nombre: str, request: Request):
    async def generador():
        manga_bd = db_buscar_manga_por_nombre(nombre)
        if manga_bd:
            capitulos = db_listar_capitulos(manga_bd["id_inmanga"])
            yield {
                "event": "cache",
                "data": json.dumps({
                    "manga": dict(manga_bd),
                    "capitulos": [dict(c) for c in capitulos],
                }, default=str)
            }
            yield {"event": "completo", "data": json.dumps({"total": len(capitulos)})}
            return

        manga = buscar_manga(nombre)
        if not manga:
            yield {"event": "error", "data": json.dumps({"mensaje": "Manga no encontrado en InManga"})}
            return

        manga_uuid = manga["Identification"]
        nombre_real = manga["Name"]
        portada     = manga["ThumbnailPath"]
        sinopsis    = manga.get("Sinopsis", "")
        estado      = manga.get("BroadcastStatusDescription", "")

        with get_db() as conn:
            db_upsert_manga(conn, manga_uuid, nombre_real, portada, sinopsis, estado)
            conn.commit()

        yield {
            "event": "encontrado",
            "data": json.dumps({
                "id_inmanga":  manga_uuid,
                "nombre":      nombre_real,
                "portada_url": portada,
                "sinopsis":    sinopsis,
                "estado":      estado,
            })
        }

        capitulos = obtener_capitulos(manga_uuid)
        total = len(capitulos)

        for i, cap in enumerate(capitulos, start=1):

            # ← Detecta desconexión antes de cada capítulo
            if await request.is_disconnected():
                print(f"⚠️ Cliente desconectado en cap {i}/{total}, cancelando.")
                return

            cap_uuid = cap["Identification"]
            numero   = float(cap["Number"])
            urls     = generar_urls_capitulo(manga_uuid, cap_uuid)

            with get_db() as conn:
                db_upsert_capitulo(conn, cap_uuid, manga_uuid, numero, json.dumps(urls))
                conn.commit()

            yield {
                "event": "capitulo",
                "data": json.dumps({
                    "numero":        numero,
                    "total_paginas": len(urls),
                    "progreso":      i,
                    "total":         total,
                })
            }

        yield {"event": "completo", "data": json.dumps({"total": total})}

    return EventSourceResponse(generador())





@app.get("/mangas/{manga_id}", response_model=MangaDetalle)
def detalle_manga(manga_id: str):
    validar_uuid(manga_id) 
    """Devuelve la info completa de un manga por su UUID."""
    manga = db_obtener_manga_por_id(manga_id)
    if not manga:
        raise HTTPException(status_code=404, detail="Manga no encontrado")
    return manga


# ──────────────────────────────────────────
# CAPÍTULOS
# ──────────────────────────────────────────

@app.get("/mangas/{manga_id}/capitulos", response_model=ListaCapitulosResponse)
def listar_capitulos(manga_id: str):
    validar_uuid(manga_id) 
    """Lista todos los capítulos de un manga."""
    capitulos = db_listar_capitulos(manga_id)
    if not capitulos:
        raise HTTPException(status_code=404, detail="No se encontraron capítulos")
    return {
        "manga_id": manga_id,
        "total_capitulos": len(capitulos),
        "capitulos": capitulos
    }


@app.get("/mangas/{manga_id}/capitulos/{numero}", response_model=PaginasCapituloResponse)
def paginas_capitulo(manga_id: str, numero: float):
    validar_uuid(manga_id)
    """Devuelve las URLs de imágenes de un capítulo específico."""
    cap = db_obtener_capitulo(manga_id, numero)
    if not cap:
        raise HTTPException(status_code=404, detail="Capítulo no encontrado")
    return {
        "manga_id": manga_id,
        "capitulo": numero,
        "total_paginas": len(cap["urls_imagenes"]),
        "paginas": cap["urls_imagenes"]
    }


@app.get("/mangas/{manga_id}/capitulos/{numero}/siguiente")
def capitulo_siguiente(manga_id: str, numero: float):
    validar_uuid(manga_id)
    """Devuelve las páginas del capítulo siguiente."""
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id_inmanga, numero, urls_imagenes
            FROM capitulos
            WHERE manga_id = %s AND numero > %s
            ORDER BY numero ASC
            LIMIT 1;
        """, (manga_id, numero))
        cap = cursor.fetchone()

    if not cap:
        raise HTTPException(status_code=404, detail="No hay capítulo siguiente")

    return {
        "manga_id":      manga_id,
        "capitulo":      cap["numero"],
        "total_paginas": len(cap["urls_imagenes"]),
        "paginas":       cap["urls_imagenes"]
    }




@app.get("/mangas/{manga_id}/capitulos/{numero}/anterior")
def capitulo_anterior(manga_id: str, numero: float):
    validar_uuid(manga_id)
    """Devuelve las páginas del capítulo anterior."""
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id_inmanga, numero, urls_imagenes
            FROM capitulos
            WHERE manga_id = %s AND numero < %s
            ORDER BY numero DESC
            LIMIT 1;
        """, (manga_id, numero))
        cap = cursor.fetchone()

    if not cap:
        raise HTTPException(status_code=404, detail="No hay capítulo anterior")

    return {
        "manga_id":      manga_id,
        "capitulo":      cap["numero"],
        "total_paginas": len(cap["urls_imagenes"]),
        "paginas":       cap["urls_imagenes"]
    }

@app.get("/novedades")
def novedades():
    """
    Devuelve los mangas con capítulos actualizados
    ordenados por el capítulo más reciente.
    """
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT DISTINCT m.id_inmanga, m.nombre, m.portada_url, m.estado,
                   MAX(c.updated_at) as ultimo_capitulo,
                   MAX(c.numero) as ultimo_numero
            FROM mangas m
            JOIN capitulos c ON c.manga_id = m.id_inmanga
            GROUP BY m.id_inmanga, m.nombre, m.portada_url, m.estado
            ORDER BY ultimo_capitulo DESC
            LIMIT 20;
        """)
        resultados = cursor.fetchall()

    return {
        "total": len(resultados),
        "resultados": resultados
    }




# ──────────────────────────────────────────
# ADMIN
# ──────────────────────────────────────────

@app.post("/admin/scrape")
def forzar_scrape(body: ScrapeRequest, background_tasks: BackgroundTasks):
    """
    Fuerza el scraping de uno o varios mangas.
    Body: { "mangas": ["naruto", "bleach"] }
    """
    if not body.mangas:
        raise HTTPException(status_code=400, detail="La lista de mangas está vacía")

    background_tasks.add_task(_scrape_y_guarda, body.mangas)

    return {"mensaje": f"Scraping iniciado para: {body.mangas}"}


@app.delete("/admin/manga/{manga_id}")
def borrar_manga(manga_id: str):
    validar_uuid(manga_id)
    """
    Borra un manga y todos sus capítulos de la BD.
    Los capítulos se borran solos por el ON DELETE CASCADE.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM mangas WHERE id_inmanga = %s RETURNING id_inmanga;",
            (manga_id,)
        )
        borrado = cursor.fetchone()
        conn.commit()

    if not borrado:
        raise HTTPException(status_code=404, detail="Manga no encontrado")

    return {"mensaje": f"Manga {manga_id} eliminado correctamente"}


@app.api_route("/ping", methods=["GET", "HEAD"])
def ping(token: str = ""):
    expected = os.getenv("PING_TOKEN", "")
    if token != expected:
        raise HTTPException(status_code=401, detail="No autorizado")
    return {"status": "awake"}

@app.post("/admin/actualizar-novedades")
def actualizar_novedades(background_tasks: BackgroundTasks):
    """
    Cron job: obtiene capítulos recientes de InManga
    y actualiza solo los mangas que ya están en BD.
    """
    background_tasks.add_task(_actualizar_novedades_task)
    return {"mensaje": "Actualización de novedades iniciada en background"}


def _actualizar_novedades_task():
    from spider import obtener_capitulos_recientes, generar_urls_capitulo

    recientes = obtener_capitulos_recientes()
    print(f"📡 {len(recientes)} capítulos recientes encontrados en InManga")

    with get_db() as conn:
        cursor = conn.cursor()

        for item in recientes:
            manga_uuid = item["manga_uuid"]
            cap_uuid   = item["cap_uuid"]
            cap_numero = float(item["cap_numero"])

            # Solo si el manga ya existe en BD
            cursor.execute(
                "SELECT id_inmanga FROM mangas WHERE id_inmanga = %s;",
                (manga_uuid,)
            )
            if not cursor.fetchone():
                print(f"  ⏭️  {item['manga_nombre']} no está en BD, ignorando.")
                continue

            # Verificar si el capítulo ya existe
            cursor.execute(
                "SELECT id_inmanga FROM capitulos WHERE id_inmanga = %s;",
                (cap_uuid,)
            )
            if cursor.fetchone():
                print(f"  ✅ Cap {cap_numero} ya existe, ignorando.")
                continue

            # Es nuevo → descargarlo
            urls = generar_urls_capitulo(manga_uuid, cap_uuid)
            cursor.execute("""
                INSERT INTO capitulos (id_inmanga, manga_id, numero, urls_imagenes, updated_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (id_inmanga) DO UPDATE SET
                    urls_imagenes = EXCLUDED.urls_imagenes,
                    updated_at    = CURRENT_TIMESTAMP;
            """, (cap_uuid, manga_uuid, cap_numero, json.dumps(urls)))

            print(f"  🆕 Cap {cap_numero} de {item['manga_nombre']} guardado.")

        conn.commit()
        cursor.close()
    print("✅ Novedades actualizadas.")








