"""Deduplicacion de noticias.

Diez portales replicando el mismo cable de agencia son **un dato, no diez**
(SPEC 6.2). Sin esto, el score se infla por popularidad mediatica en vez de
por senal: una noticia muy replicada pesaria diez veces mas que una exclusiva.

Dos niveles:

  1. Hash exacto del titular normalizado. Atrapa la replica literal.
  2. Similitud de Jaccard sobre los tokens. Atrapa al portal que le cambia
     tres palabras al titular de agencia.

Ambos dentro de una ventana de 48 h: el mismo titular seis meses despues es
otra noticia, no un duplicado.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos.noticia import Noticia
from investing_bot.normalizador.entidades import normalizar_texto

VENTANA_DUPLICADOS = timedelta(hours=48)
UMBRAL_JACCARD = 0.85

# Palabras vacias en ingles y espanol. Quitarlas hace que "Apple beats
# estimates" y "Apple beats the estimates" produzcan el mismo hash.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "its",
        "it",
        "this",
        "that",
        "these",
        "those",
        "will",
        "would",
        "has",
        "have",
        "had",
        "after",
        "over",
        "amid",
        "says",
        "said",
        "new",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "de",
        "del",
        "y",
        "o",
        "pero",
        "si",
        "en",
        "con",
        "por",
        "para",
        "su",
        "sus",
        "es",
        "son",
        "era",
        "fue",
        "ser",
        "este",
        "esta",
        "estos",
        "estas",
        "que",
        "se",
        "tras",
        "sobre",
        "dice",
        "dijo",
    }
)


@dataclass(slots=True)
class GrupoDuplicados:
    """Un titular original y las replicas que se le atribuyeron."""

    original: int
    replicas: list[int] = field(default_factory=list)


def tokens_significativos(titulo: str) -> list[str]:
    """Tokens del titular sin puntuacion, sin tildes y sin palabras vacias.

    Los tokens de una sola letra se descartan por ruidosos, pero **los digitos
    se conservan siempre**: en un titular financiero la cifra suele ser la
    noticia. Sin esa excepcion, "recalls 2 million" y "recalls 3 million"
    producirian el mismo hash y una de las dos se perderia como duplicado.
    """
    return [
        t
        for t in normalizar_texto(titulo).split()
        if t not in STOPWORDS and (len(t) > 1 or t.isdigit())
    ]


def hash_titular(titulo: str) -> str:
    """Huella estable de un titular.

    Conserva el orden de las palabras a proposito: ordenarlas haria que
    "Nvidia supera a AMD" y "AMD supera a Nvidia" colisionaran, que son
    noticias opuestas.
    """
    return hashlib.sha256(" ".join(tokens_significativos(titulo)).encode("utf-8")).hexdigest()


def similitud_jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Proporcion de tokens compartidos entre dos titulares."""
    conjunto_a, conjunto_b = set(a), set(b)
    if not conjunto_a or not conjunto_b:
        return 0.0
    interseccion = len(conjunto_a & conjunto_b)
    union = len(conjunto_a | conjunto_b)
    return interseccion / union


def son_casi_iguales(titulo_a: str, titulo_b: str, umbral: float = UMBRAL_JACCARD) -> bool:
    """Decide si dos titulares son la misma noticia reescrita."""
    return similitud_jaccard(tokens_significativos(titulo_a), tokens_significativos(titulo_b)) >= (
        umbral
    )


def agrupar_lote(
    titulares: Sequence[tuple[int, str, datetime]],
    ventana: timedelta = VENTANA_DUPLICADOS,
    umbral: float = UMBRAL_JACCARD,
) -> list[GrupoDuplicados]:
    """Agrupa duplicados dentro de un mismo lote, sin tocar la base.

    Recibe tuplas `(id, titulo, event_at)` y devuelve un grupo por noticia
    original. Gana el mas antiguo: el primero en publicar es el original y
    el resto son replicas.
    """
    ordenados = sorted(titulares, key=lambda t: t[2])
    grupos: list[GrupoDuplicados] = []
    representantes: list[tuple[int, list[str], datetime]] = []

    for identificador, titulo, momento in ordenados:
        tokens = tokens_significativos(titulo)
        for indice, (_, tokens_rep, momento_rep) in enumerate(representantes):
            if momento - momento_rep > ventana:
                continue
            if similitud_jaccard(tokens, tokens_rep) >= umbral:
                grupos[indice].replicas.append(identificador)
                break
        else:
            representantes.append((identificador, tokens, momento))
            grupos.append(GrupoDuplicados(original=identificador))

    return grupos


async def buscar_original(
    sesion: AsyncSession,
    titulo: str,
    event_at: datetime,
    ventana: timedelta = VENTANA_DUPLICADOS,
    umbral: float = UMBRAL_JACCARD,
) -> Noticia | None:
    """Busca en la base una noticia ya guardada que sea la misma que esta.

    Primero por hash exacto, que es un indice; solo si falla se comparan los
    tokens de las noticias de la ventana, que es mas caro.
    """
    huella = hash_titular(titulo)
    desde = event_at - ventana
    hasta = event_at + ventana

    exacta = await sesion.scalar(
        sa.select(Noticia)
        .where(
            Noticia.hash_contenido == huella,
            Noticia.event_at >= desde,
            Noticia.event_at <= hasta,
        )
        .limit(1)
    )
    if exacta is not None:
        return exacta

    tokens = tokens_significativos(titulo)
    candidatas = (
        await sesion.scalars(
            sa.select(Noticia).where(
                Noticia.event_at >= desde,
                Noticia.event_at <= hasta,
                Noticia.es_duplicado.is_(False),
            )
        )
    ).all()

    for candidata in candidatas:
        if similitud_jaccard(tokens, tokens_significativos(candidata.titulo)) >= umbral:
            return candidata
    return None
