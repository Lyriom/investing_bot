"""Motor, sesiones y `Base` declarativa de SQLAlchemy 2.0.

El motor se crea de forma perezosa para que los tests puedan apuntar a otra
base (sqlite en memoria) antes del primer uso.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from investing_bot.config import obtener_config

# --- Tipos portables -------------------------------------------------------
# El sistema corre sobre PostgreSQL, pero la suite de tests usa sqlite en
# memoria para no depender de un contenedor. Estas variantes permiten un unico
# juego de modelos para ambos motores.

IdEntero = sa.BigInteger().with_variant(sa.Integer, "sqlite")
"""Clave primaria autoincremental (bigserial en PostgreSQL)."""

JsonPortable = sa.JSON().with_variant(postgresql.JSONB, "postgresql")
"""JSONB en PostgreSQL, JSON generico en el resto."""

MarcaTiempo = sa.DateTime(timezone=True)
"""Timestamp con zona horaria. Todo instante se guarda en UTC."""


def ahora_utc() -> datetime:
    """Instante actual en UTC, con tzinfo explicito."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base declarativa comun a todos los modelos."""

    type_annotation_map = {  # noqa: RUF012
        dict[str, Any]: JsonPortable,
    }


# --- Motor y sesiones ------------------------------------------------------

_motor: AsyncEngine | None = None
_fabrica: async_sessionmaker[AsyncSession] | None = None


def obtener_motor() -> AsyncEngine:
    """Devuelve el motor asincrono del proceso, creandolo si hace falta."""
    global _motor
    if _motor is None:
        config = obtener_config()
        _motor = create_async_engine(
            config.url_bd_async,
            echo=False,
            pool_pre_ping=True,
            future=True,
        )
    return _motor


def obtener_fabrica_sesiones() -> async_sessionmaker[AsyncSession]:
    """Devuelve la fabrica de sesiones del proceso."""
    global _fabrica
    if _fabrica is None:
        _fabrica = async_sessionmaker(
            bind=obtener_motor(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _fabrica


def configurar_motor(motor: AsyncEngine) -> None:
    """Reemplaza el motor global. Uso exclusivo de los tests."""
    global _motor, _fabrica
    _motor = motor
    _fabrica = async_sessionmaker(bind=motor, expire_on_commit=False, autoflush=False)


async def cerrar_motor() -> None:
    """Cierra el pool de conexiones y limpia el estado global."""
    global _motor, _fabrica
    if _motor is not None:
        await _motor.dispose()
    _motor = None
    _fabrica = None


@asynccontextmanager
async def sesion_bd() -> AsyncIterator[AsyncSession]:
    """Context manager de sesion: commit al salir bien, rollback al fallar."""
    fabrica = obtener_fabrica_sesiones()
    async with fabrica() as sesion:
        try:
            yield sesion
            await sesion.commit()
        except Exception:
            await sesion.rollback()
            raise
