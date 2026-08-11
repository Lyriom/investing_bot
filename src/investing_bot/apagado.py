"""Apagado ordenado ante senales del sistema."""

from __future__ import annotations

import asyncio
import signal

from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

SENALES = (signal.SIGTERM, signal.SIGINT)


def instalar_senales(detener: asyncio.Event) -> None:
    """Hace que SIGTERM y SIGINT despierten al proceso en vez de matarlo.

    `docker stop` manda SIGTERM y espera 10 s antes de recurrir a SIGKILL. Sin
    este manejador el proceso agota esa espera en cada despliegue y muere de
    golpe, dejando el pool de conexiones a medio cerrar y, si habia una ingesta
    en curso, un hueco silencioso en los datos.
    """
    bucle = asyncio.get_running_loop()
    for senal in SENALES:
        try:
            bucle.add_signal_handler(senal, detener.set)
        except NotImplementedError:  # pragma: no cover - Windows
            log.warning("senal_no_soportada", senal=senal.name)
