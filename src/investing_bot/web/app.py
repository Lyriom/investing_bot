"""Aplicacion FastAPI del dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from investing_bot.config import obtener_config
from investing_bot.db import cerrar_motor
from investing_bot.registro import configurar_logs, obtener_logger
from investing_bot.web.rutas import panel

log = obtener_logger(__name__)


@asynccontextmanager
async def ciclo_vida(app: FastAPI) -> AsyncIterator[None]:
    """Arranque y apagado ordenado de la aplicacion."""
    log.info("dashboard_iniciado")
    yield
    await cerrar_motor()
    log.info("dashboard_detenido")


def crear_app() -> FastAPI:
    """Construye la aplicacion FastAPI del dashboard."""
    config = obtener_config()
    configurar_logs(config.nivel_log, config.entorno)

    app = FastAPI(
        title="investing_bot",
        description="Panel de control. El sistema sugiere; la persona decide.",
        version="0.1.0",
        lifespan=ciclo_vida,
    )
    app.include_router(panel.router)
    return app


app = crear_app()
