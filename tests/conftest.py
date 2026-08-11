"""Fixtures comunes de la suite.

La suite corre sobre sqlite en memoria: los tests no deben depender de un
contenedor levantado ni, bajo ninguna circunstancia, golpear APIs externas.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import investing_bot.modelos  # noqa: F401  llena Base.metadata
from investing_bot.db import Base, cerrar_motor, configurar_motor, obtener_fabrica_sesiones


@pytest_asyncio.fixture
async def motor() -> AsyncIterator[AsyncEngine]:
    """Motor sqlite en memoria, compartido por todas las conexiones del test.

    `StaticPool` es imprescindible: sin el, cada conexion abriria su propia
    base en memoria y las tablas creadas aqui no existirian en la siguiente.
    """
    motor = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with motor.begin() as conexion:
        await conexion.run_sync(Base.metadata.create_all)

    configurar_motor(motor)
    try:
        yield motor
    finally:
        await cerrar_motor()


@pytest_asyncio.fixture
async def fabrica(motor: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Fabrica de sesiones ligada al motor de test."""
    return obtener_fabrica_sesiones()


@pytest_asyncio.fixture
async def sesion(
    fabrica: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Sesion de base de datos para un test."""
    async with fabrica() as sesion:
        yield sesion
