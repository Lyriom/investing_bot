"""Ingestores de datos externos.

FASE 0 implementa unicamente `precios`. Los ingestores de noticias, Reddit y
Congreso corresponden a la FASE 1 y todavia no existen: el SPEC manda trabajar
una fase a la vez.
"""

from investing_bot.ingestores.base import Ingestor, ResultadoIngesta
from investing_bot.ingestores.precios import IngestorPrecios

__all__ = ["Ingestor", "IngestorPrecios", "ResultadoIngesta"]
