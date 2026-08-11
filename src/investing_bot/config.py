"""Configuracion del sistema.

Todos los secretos entran por variables de entorno o por el archivo `.env`
local, jamas por el repositorio (invariante I5).
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ_PAQUETE = Path(__file__).resolve().parent
RAIZ_PROYECTO = RAIZ_PAQUETE.parent.parent


class Configuracion(BaseSettings):
    """Parametros del sistema, leidos del entorno o de `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Entorno ---------------------------------------------------------
    entorno: str = "desarrollo"
    nivel_log: str = "INFO"

    # --- Base de datos ---------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "investing_bot"
    postgres_user: str = "investing"
    postgres_password: str = ""
    url_bd: str | None = None

    # --- Telegram --------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id_autorizado: int = 0

    # --- Servidor web ----------------------------------------------------
    web_host: str = "0.0.0.0"
    web_puerto: int = 8000

    # --- Parametros de riesgo -------------------------------------------
    # PROVISIONALES. Fijos y arbitrarios a proposito: el invariante I4 prohibe
    # optimizarlos antes de tener el backtester validado (FASE 2).
    capital_total_usd: Decimal = Decimal("150")
    max_posiciones_abiertas: int = 5
    min_tamano_posicion_usd: Decimal = Decimal("50")
    max_pct_por_posicion: Decimal = Decimal("0.25")
    max_posiciones_por_sector: int = 2
    stop_loss_pct: Decimal = Decimal("0.08")
    max_perdida_mensual_pct: Decimal = Decimal("0.10")
    min_volumen_diario: int = 1_000_000
    min_precio_accion: Decimal = Decimal("5.00")
    max_sugerencias_por_dia: int = 2
    dias_cooldown_mismo_ticker: int = 5
    costo_clearing_usd: Decimal = Decimal("0.15")
    spread_estimado_pct: Decimal = Decimal("0.001")
    max_costo_friccion_pct: Decimal = Decimal("0.01")

    # --- Ingesta ---------------------------------------------------------
    dias_historial_precios_inicial: int = 90
    reintentos_max: int = 4
    backoff_base_seg: float = 1.5

    # --- Zonas horarias --------------------------------------------------
    zona_horaria_mercado: str = "America/New_York"
    zona_horaria_operador: str = "America/Guayaquil"

    # --- Claves de fuentes externas (se usan desde la FASE 1) ------------
    finnhub_api_key: str = ""
    marketaux_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "investing_bot/0.1"

    # --- Rutas -----------------------------------------------------------
    archivo_whitelist: Path = Field(default=RAIZ_PAQUETE / "semillas" / "whitelist.json")

    @property
    def url_bd_async(self) -> str:
        """URL de conexion asincrona (asyncpg) que usa la aplicacion."""
        if self.url_bd:
            return self.url_bd
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def telegram_configurado(self) -> bool:
        """El bot solo puede arrancar con token y chat autorizado presentes."""
        return bool(self.telegram_bot_token) and self.telegram_chat_id_autorizado != 0


@lru_cache(maxsize=1)
def obtener_config() -> Configuracion:
    """Devuelve la configuracion cacheada del proceso."""
    return Configuracion()


def limpiar_cache_config() -> None:
    """Invalida la configuracion cacheada. Uso exclusivo de los tests."""
    obtener_config.cache_clear()
