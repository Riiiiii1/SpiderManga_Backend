import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from dotenv import load_dotenv
load_dotenv()
# Lee de variable de entorno (configúrala en Render como DATABASE_URL)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://spidermanga_user:6V0JHnAH96b4iEVbQQBku7hjTIDZLhb9@dpg-d7v2488g4nts73ff8fg0-a.oregon-postgres.render.com/spidermanga"
)


# ──────────────────────────────────────────
# CONEXIÓN
# ──────────────────────────────────────────

@contextmanager
def get_db():
    """Abre y cierra la conexión automáticamente. Usar con 'with get_db() as conn'."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


# ──────────────────────────────────────────
# QUERIES — MANGAS
# ──────────────────────────────────────────

def db_listar_mangas() -> list:
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id_inmanga, nombre, portada_url, estado, updated_at
            FROM mangas
            ORDER BY nombre;
        """)
        return cursor.fetchall()


def db_buscar_manga_por_nombre(nombre: str) -> dict | None:
    """Búsqueda parcial e insensible a mayúsculas."""
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM mangas WHERE LOWER(nombre) LIKE LOWER(%s) LIMIT 1;",
            (f"%{nombre}%",)
        )
        return cursor.fetchone()


def db_obtener_manga_por_id(manga_id: str) -> dict | None:
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM mangas WHERE id_inmanga = %s;",
            (manga_id,)
        )
        return cursor.fetchone()


def db_upsert_manga(conn, manga_id: str, nombre: str, portada: str, sinopsis: str, estado: str):
    """Inserta o actualiza un manga. Recibe conexión abierta (para usar en transacciones)."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO mangas (id_inmanga, nombre, portada_url, sinopsis, estado, updated_at)
        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (id_inmanga) DO UPDATE SET
            nombre      = EXCLUDED.nombre,
            portada_url = EXCLUDED.portada_url,
            sinopsis    = EXCLUDED.sinopsis,
            estado      = EXCLUDED.estado,
            updated_at  = CURRENT_TIMESTAMP;
    """, (manga_id, nombre, portada, sinopsis, estado))
    cursor.close()


# ──────────────────────────────────────────
# QUERIES — CAPÍTULOS
# ──────────────────────────────────────────

def db_listar_capitulos(manga_id: str) -> list:
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id_inmanga, numero, urls_imagenes, updated_at
            FROM capitulos
            WHERE manga_id = %s
            ORDER BY numero ASC;
        """, (manga_id,))
        return cursor.fetchall()


def db_obtener_capitulo(manga_id: str, numero: float) -> dict | None:
    with get_db() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id_inmanga, numero, urls_imagenes
            FROM capitulos
            WHERE manga_id = %s AND numero = %s;
        """, (manga_id, numero))
        return cursor.fetchone()


def db_upsert_capitulo(conn, cap_id: str, manga_id: str, numero: float, urls_json: str):
    """Inserta o actualiza un capítulo. Recibe conexión abierta."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO capitulos (id_inmanga, manga_id, numero, urls_imagenes, updated_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (id_inmanga) DO UPDATE SET
            urls_imagenes = EXCLUDED.urls_imagenes,
            updated_at    = CURRENT_TIMESTAMP;
    """, (cap_id, manga_id, numero, urls_json))
    cursor.close()