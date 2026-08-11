"""Planificador de tareas (APScheduler).

FASE 0 registra un unico job: la ingesta diaria de precios tras el cierre del
mercado. Los jobs de noticias, Reddit, Congreso y el digest diario se agregan
en las fases 1 y 3.
"""

from __future__ import annotations

import asyncio
import signal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from investing_bot.config import obtener_config
from investing_bot.db import cerrar_motor
from investing_bot.ingestores.precios import IngestorPrecios
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

# La ingesta corre a las 17:00 ET: una hora despues del cierre, con margen
# para que la barra del dia este consolidada en la fuente.
HORA_INGESTA_PRECIOS = 17
MINUTO_INGESTA_PRECIOS = 0


async def job_ingesta_precios() -> None:
    """Ingesta diaria de precios. Nunca propaga excepciones."""
    ingestor = IngestorPrecios()
    await ingestor.ejecutar_registrado()


def construir_planificador() -> AsyncIOScheduler:
    """Arma el planificador con los jobs de la fase actual."""
    config = obtener_config()
    zona = ZoneInfo(config.zona_horaria_mercado)
    planificador = AsyncIOScheduler(timezone=zona)

    planificador.add_job(
        job_ingesta_precios,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=HORA_INGESTA_PRECIOS,
            minute=MINUTO_INGESTA_PRECIOS,
            timezone=zona,
        ),
        id="ingesta_precios",
        name="Ingesta diaria de precios",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
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
