"""Planificador de tareas (APScheduler).

Cadencia del SPEC 6.1 y 8. Todos los jobs corren en horario de Nueva York,
porque lo que marca el ritmo es el mercado, no el operador.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from investing_bot.config import obtener_config
from investing_bot.db import cerrar_motor
from investing_bot.ingestores import INGESTORES
from investing_bot.registro import obtener_logger
from investing_bot.servicios.digest import ejecutar_digest_diario

log = obtener_logger(__name__)

# La ingesta de precios corre a las 17:00 ET: una hora despues del cierre, con
# margen para que la barra del dia este consolidada en la fuente.
HORA_INGESTA_PRECIOS = 17
MINUTO_INGESTA_PRECIOS = 0

# El digest sale a las 18:15 ET, con los precios del dia ya en la base.
HORA_DIGEST = 18
MINUTO_DIGEST = 15


def _job_ingestor(nombre: str) -> Callable[[], Awaitable[None]]:
    """Crea el callable de un ingestor. Nunca propaga excepciones."""

    async def job() -> None:
        ingestor = INGESTORES[nombre]()
        await ingestor.ejecutar_registrado()

    job.__name__ = f"job_ingesta_{nombre}"
    return job


async def job_digest_diario() -> None:
    """Digest diario. Un fallo aqui no debe tumbar el planificador."""
    try:
        await ejecutar_digest_diario()
    except Exception:  # noqa: BLE001 - el planificador sobrevive a un digest roto
        log.exception("digest_fallo")


def construir_planificador() -> AsyncIOScheduler:
    """Arma el planificador con todos los jobs."""
    config = obtener_config()
    zona = ZoneInfo(config.zona_horaria_mercado)
    planificador = AsyncIOScheduler(timezone=zona)

    comun = {"replace_existing": True, "coalesce": True, "max_instances": 1}

    planificador.add_job(
        _job_ingestor("precios"),
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=HORA_INGESTA_PRECIOS,
            minute=MINUTO_INGESTA_PRECIOS,
            timezone=zona,
        ),
        id="ingesta_precios",
        name="Ingesta diaria de precios",
        misfire_grace_time=3600,
        **comun,
    )

    planificador.add_job(
        _job_ingestor("noticias"),
        trigger=IntervalTrigger(hours=4, timezone=zona),
        id="ingesta_noticias",
        name="Ingesta de noticias (cada 4 h)",
        misfire_grace_time=1800,
        **comun,
    )

    planificador.add_job(
        _job_ingestor("reddit"),
        trigger=IntervalTrigger(hours=6, timezone=zona),
        id="ingesta_reddit",
        name="Ingesta de Reddit (cada 6 h)",
        misfire_grace_time=1800,
        **comun,
    )

    planificador.add_job(
        _job_ingestor("congreso"),
        trigger=CronTrigger(hour=6, minute=30, timezone=zona),
        id="ingesta_congreso",
        name="Ingesta diaria del Congreso",
        misfire_grace_time=7200,
        **comun,
    )

    planificador.add_job(
        job_digest_diario,
        trigger=CronTrigger(
            day_of_week="mon-fri", hour=HORA_DIGEST, minute=MINUTO_DIGEST, timezone=zona
        ),
        id="digest_diario",
        name="Digest diario de sugerencias",
        misfire_grace_time=3600,
        **comun,
    )

    return planificador


def _instalar_senales(detener: asyncio.Event) -> None:
    """Hace que SIGTERM y SIGINT despierten al proceso en vez de matarlo.

    `docker stop` manda SIGTERM y espera 10 s antes de recurrir a SIGKILL. Sin
    este manejador el proceso agota esa espera en cada despliegue y muere de
    golpe, dejando el pool de conexiones a medio cerrar.
    """
    bucle = asyncio.get_running_loop()
    for senal in (signal.SIGTERM, signal.SIGINT):
        try:
            bucle.add_signal_handler(senal, detener.set)
        except NotImplementedError:  # pragma: no cover - Windows
            log.warning("senal_no_soportada", senal=senal.name)


async def ejecutar_planificador() -> None:
    """Arranca el planificador y lo mantiene vivo hasta recibir una senal."""
    detener = asyncio.Event()
    _instalar_senales(detener)

    planificador = construir_planificador()
    planificador.start()
    for job in planificador.get_jobs():
        log.info("job_registrado", id=job.id, proxima_ejecucion=str(job.next_run_time))

    try:
        await detener.wait()
        log.info("senal_de_apagado_recibida")
    except asyncio.CancelledError:
        log.info("planificador_cancelado")
    finally:
        # `wait=True`: si hay una ingesta a mitad de camino, se la deja
        # terminar antes de cerrar la base. Perder una corrida por un
        # despliegue seria dejar un hueco silencioso en los datos.
        planificador.shutdown(wait=True)
        await cerrar_motor()
        log.info("planificador_detenido")
