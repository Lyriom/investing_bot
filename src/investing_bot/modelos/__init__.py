"""Modelos SQLAlchemy.

Importar todo aqui garantiza que `Base.metadata` este completo cuando Alembic
lo consulte para autogenerar migraciones.
"""

from investing_bot.modelos.congreso import CongresoTrade
from investing_bot.modelos.corrida_ingesta import CorridaIngesta
from investing_bot.modelos.ejecucion import Ejecucion
from investing_bot.modelos.estado_sistema import (
    CLAVE_CHAT_VINCULADO,
    CLAVE_ENVIOS_PAUSADOS,
    EstadoSistema,
)
from investing_bot.modelos.noticia import Noticia
from investing_bot.modelos.posicion_sombra import PosicionSombra
from investing_bot.modelos.precio import PrecioDiario
from investing_bot.modelos.reddit import RedditDiario
from investing_bot.modelos.senal import Senal
from investing_bot.modelos.sugerencia import Sugerencia
from investing_bot.modelos.ticker import Ticker

__all__ = [
    "CLAVE_CHAT_VINCULADO",
    "CLAVE_ENVIOS_PAUSADOS",
    "CongresoTrade",
    "CorridaIngesta",
    "Ejecucion",
    "EstadoSistema",
    "Noticia",
    "PosicionSombra",
    "PrecioDiario",
    "RedditDiario",
    "Senal",
    "Sugerencia",
    "Ticker",
]
