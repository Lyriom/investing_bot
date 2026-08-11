"""Configuracion de logs estructurados.

Regla del proyecto: nada de `print()`. Todo sale por structlog, en JSON cuando
el entorno no es de desarrollo, para que sea grepeable y agregable.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configurar_logs(nivel: str = "INFO", entorno: str = "desarrollo") -> None:
    """Deja structlog listo para todo el proceso.

    En `desarrollo` imprime con colores y alineado; en cualquier otro entorno
    emite JSON de una linea por evento.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, nivel.upper(), logging.INFO),
    )

    procesadores: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if entorno == "desarrollo":
        procesadores.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        procesadores.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=procesadores,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, nivel.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def obtener_logger(nombre: str) -> structlog.stdlib.BoundLogger:
    """Devuelve un logger ligado al nombre del modulo."""
    return structlog.get_logger(nombre)
