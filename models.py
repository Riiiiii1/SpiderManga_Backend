from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ──────────────────────────────────────────
# MANGA
# ──────────────────────────────────────────

class MangaResumen(BaseModel):
    """Lo que se muestra en el listado general."""
    id_inmanga: str
    nombre: str
    portada_url: Optional[str] = None
    estado: Optional[str] = None
    updated_at: Optional[datetime] = None


class MangaDetalle(MangaResumen):
    """Info completa incluyendo sinopsis."""
    sinopsis: Optional[str] = None


# ──────────────────────────────────────────
# CAPÍTULO
# ──────────────────────────────────────────

class CapituloResumen(BaseModel):
    """Capítulo sin las URLs (para el listado)."""
    id_inmanga: str
    numero: float
    updated_at: Optional[datetime] = None


class CapituloDetalle(CapituloResumen):
    """Capítulo con las URLs de imágenes."""
    urls_imagenes: list[str] = []


# ──────────────────────────────────────────
# RESPONSES
# ──────────────────────────────────────────

class ListaMangasResponse(BaseModel):
    total: int
    resultados: list[MangaResumen]


class ListaCapitulosResponse(BaseModel):
    manga_id: str
    total_capitulos: int
    capitulos: list[CapituloResumen]


class PaginasCapituloResponse(BaseModel):
    manga_id: str
    capitulo: float
    total_paginas: int
    paginas: list[str]


class BusquedaResponse(BaseModel):
    source: str  # "cache" o "scraping"
    data: Optional[MangaDetalle] = None
    mensaje: Optional[str] = None


# ──────────────────────────────────────────
# REQUESTS (lo que recibe la API)
# ──────────────────────────────────────────

class ScrapeRequest(BaseModel):
    """Body del endpoint /admin/scrape"""
    mangas: list[str]