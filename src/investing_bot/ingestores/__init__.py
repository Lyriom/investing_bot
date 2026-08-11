"""Ingestores de datos externos.

Los cuatro del SPEC 6.1. Cada uno es idempotente, tolerante a fallo y
consciente del rate limit de su fuente.
"""

from investing_bot.ingestores.base import Ingestor, ResultadoIngesta
from investing_bot.ingestores.congreso import IngestorCongreso
from investing_bot.ingestores.noticias import IngestorNoticias
from investing_bot.ingestores.precios import IngestorPrecios
from investing_bot.ingestores.reddit import IngestorReddit

# Nombre -> clase. Lo usan el CLI y el planificador para no repetir la lista.
INGESTORES: dict[str, type[Ingestor]] = {
    "precios": IngestorPrecios,
    "noticias": IngestorNoticias,
    "reddit": IngestorReddit,
    "congreso": IngestorCongreso,
}

__all__ = [
    "INGESTORES",
    "Ingestor",
    "IngestorCongreso",
    "IngestorNoticias",
    "IngestorPrecios",
    "IngestorReddit",
    "ResultadoIngesta",
]
