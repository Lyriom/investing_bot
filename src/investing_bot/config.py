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

# Prefijos que entregan los PaaS al crear una base gestionada.
_PREFIJOS_SINCRONOS = ("postgres://", "postgresql://")
_PREFIJO_ASYNC = "postgresql+asyncpg://"


def forzar_driver_asyncpg(url: str) -> str:
    """Reescribe la URL para que use asyncpg.

    Easypanel, Railway y Heroku entregan la cadena de conexion como
    `postgres://...` o `postgresql://...`. SQLAlchemy resolveria eso a
    psycopg2, un driver sincrono que este proyecto no instala, y el arranque
    fallaria con un error que no dice nada util. Pegar la URL tal cual como la
    da el panel tiene que funcionar.
    """
    for prefijo in _PREFIJOS_SINCRONOS:
        if url.startswith(prefijo):
            return _PREFIJO_ASYNC + url[len(prefijo) :]
    return url


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
    dias_historial_congreso: int = 180
    dias_historial_noticias: int = 7
    horas_ventana_reddit: int = 24
    subreddits: str = "wallstreetbets,stocks,investing"
    # Fuentes del Congreso. Configurables porque los datasets publicos de
    # stock-watcher dejaron de responder (403) en agosto de 2026: asi se puede
    # apuntar a un espejo sin tocar codigo. Ver docs/bitacora.md.
    url_congreso_camara: str = (
        "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
    )
    url_congreso_senado: str = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
    limite_posts_reddit: int = 300
    reintentos_max: int = 4
    backoff_base_seg: float = 1.5
    # Fuerza el lexico aunque FinBERT este instalado. Util para tests y para
    # servidores donde cargar torch no es viable.
    forzar_sentimiento_lexico: bool = False

    # --- Senales (FASE 3) ------------------------------------------------
    # Pesos PROVISIONALES, fijos y arbitrarios (invariante I4). No se tocan
    # hasta que el backtester de la FASE 2 diga algo.
    version_modelo: str = "pesos-v1"
    peso_deriva_noticias: float = 0.40
    peso_velocidad_reddit: float = 0.25
    peso_consenso_congreso: float = 0.15
    umbral_sugerencia: int = 60
    # En regimen de riesgo el score por encima de 50 se multiplica por esto.
    multiplicador_regimen_riesgo: float = 0.75
    dias_media_movil_regimen: int = 200
    symbol_referencia_regimen: str = "SPY"

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
            return forzar_driver_asyncpg(self.url_bd)
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
