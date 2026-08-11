"""Utilidades compartidas por los ingestores: upsert idempotente y reintentos."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.db import Base
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

T = TypeVar("T")


@dataclass(slots=True)
class ResultadoUpsert:
    """Conteo del efecto de un upsert."""

    nuevas: int = 0
    actualizadas: int = 0
    sin_cambios: int = 0


def _normalizar_para_comparar(valor: Any) -> Any:
    """Lleva un valor a una forma comparable entre lo que viene y lo que hay.

    Hace falta porque sqlite devuelve datetimes sin zona y PostgreSQL con
    zona; sin esta normalizacion, cada corrida marcaria todo como cambiado.
    """
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)
    if isinstance(valor, float):
        return Decimal(str(valor))
    return valor


def _clave(fila: dict[str, Any], columnas: Sequence[str]) -> tuple[Any, ...]:
    return tuple(fila[c] for c in columnas)


async def upsert_filas(
    sesion: AsyncSession,
    modelo: type[Base],
    filas: Sequence[dict[str, Any]],
    columnas_clave: Sequence[str],
    columnas_actualizar: Sequence[str],
) -> ResultadoUpsert:
    """Inserta filas nuevas y actualiza las existentes, sin duplicar.

    Es la pieza que hace idempotentes a los ingestores: correr dos veces el
    mismo dia no crea filas repetidas. Deliberadamente no usa
    `ON CONFLICT` de PostgreSQL, para que la misma ruta de codigo funcione en
    la suite de tests (sqlite) y en produccion, y para poder distinguir con
    exactitud entre fila nueva, actualizada y sin cambios.

    Devuelve el conteo de cada caso.
    """
    resultado = ResultadoUpsert()
    if not filas:
        return resultado

    # Si la fuente manda la misma clave dos veces en un mismo lote, gana la ultima.
    unicas: dict[tuple[Any, ...], dict[str, Any]] = {}
    for fila in filas:
        unicas[_clave(fila, columnas_clave)] = fila

    # Se acota la busqueda por la primera columna clave (p. ej. `symbol`) y se
    # afina en memoria por la tupla completa: portable a cualquier dialecto.
    primera = columnas_clave[0]
    valores_primera = {fila[primera] for fila in unicas.values()}

    columnas_leer = list(dict.fromkeys([*columnas_clave, *columnas_actualizar]))
    consulta = sa.select(*[getattr(modelo, c) for c in columnas_leer]).where(
        getattr(modelo, primera).in_(valores_primera)
    )
    existentes: dict[tuple[Any, ...], dict[str, Any]] = {}
    for registro in (await sesion.execute(consulta)).mappings():
        datos = dict(registro)
        existentes[_clave(datos, columnas_clave)] = datos

    nuevas: list[dict[str, Any]] = []
    for clave, fila in unicas.items():
        actual = existentes.get(clave)
        if actual is None:
            nuevas.append(fila)
            continue

        cambios = {
            c: fila[c]
            for c in columnas_actualizar
            if c in fila
            and _normalizar_para_comparar(fila[c]) != _normalizar_para_comparar(actual.get(c))
        }
        if not cambios:
            resultado.sin_cambios += 1
            continue

        condicion = sa.and_(
            *[getattr(modelo, c) == valor for c, valor in zip(columnas_clave, clave, strict=True)]
        )
        await sesion.execute(sa.update(modelo).where(condicion).values(**cambios))
        resultado.actualizadas += 1

    if nuevas:
        await sesion.execute(sa.insert(modelo), nuevas)
        resultado.nuevas = len(nuevas)

    return resultado


async def con_reintentos(
    operacion: Callable[[], Awaitable[T]],
    *,
    intentos: int = 4,
    base_seg: float = 1.5,
    descripcion: str = "operacion",
) -> T:
    """Ejecuta `operacion` con backoff exponencial y jitter.

    Las fuentes gratuitas tienen limites de tasa estrictos; reintentar sin
    jitter sincroniza los reintentos y empeora el problema.
    """
    ultimo_error: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            return await operacion()
        except Exception as exc:  # noqa: BLE001 - se re-lanza tras agotar intentos
            ultimo_error = exc
            if intento == intentos:
                break
            espera = base_seg**intento + random.uniform(0, base_seg)
            log.warning(
                "reintento",
                descripcion=descripcion,
                intento=intento,
                de=intentos,
                espera_seg=round(espera, 2),
                error=str(exc),
            )
            await asyncio.sleep(espera)

    assert ultimo_error is not None
    raise ultimo_error
