"""Tests del upsert idempotente y del backoff con reintentos."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.ingestores.utilidades import con_reintentos, upsert_filas
from investing_bot.modelos import Ticker


async def _cuantos(sesion: AsyncSession) -> int:
    return int(await sesion.scalar(sa.select(sa.func.count()).select_from(Ticker)) or 0)


async def test_upsert_inserta_lo_nuevo(sesion: AsyncSession) -> None:
    efecto = await upsert_filas(
        sesion,
        Ticker,
        [
            {"symbol": "AAPL", "nombre": "Apple Inc.", "en_whitelist": True},
            {"symbol": "MSFT", "nombre": "Microsoft", "en_whitelist": True},
        ],
        columnas_clave=("symbol",),
        columnas_actualizar=("nombre", "en_whitelist"),
    )
    assert (efecto.nuevas, efecto.actualizadas, efecto.sin_cambios) == (2, 0, 0)
    assert await _cuantos(sesion) == 2


async def test_upsert_repetido_no_duplica_ni_toca_nada(sesion: AsyncSession) -> None:
    """Correr dos veces el mismo lote deja la base igual (SPEC 6.1: idempotencia)."""
    filas = [{"symbol": "AAPL", "nombre": "Apple Inc.", "en_whitelist": True}]
    claves = {"columnas_clave": ("symbol",), "columnas_actualizar": ("nombre", "en_whitelist")}

    await upsert_filas(sesion, Ticker, filas, **claves)  # type: ignore[arg-type]
    segundo = await upsert_filas(sesion, Ticker, filas, **claves)  # type: ignore[arg-type]

    assert (segundo.nuevas, segundo.actualizadas, segundo.sin_cambios) == (0, 0, 1)
    assert await _cuantos(sesion) == 1


async def test_upsert_actualiza_solo_lo_que_cambio(sesion: AsyncSession) -> None:
    claves = {"columnas_clave": ("symbol",), "columnas_actualizar": ("nombre", "sector")}
    await upsert_filas(
        sesion,
        Ticker,
        [{"symbol": "AAPL", "nombre": "Apple", "sector": "Tecnologia"}],
        **claves,  # type: ignore[arg-type]
    )
    efecto = await upsert_filas(
        sesion,
        Ticker,
        [{"symbol": "AAPL", "nombre": "Apple Inc.", "sector": "Tecnologia"}],
        **claves,  # type: ignore[arg-type]
    )
    assert (efecto.nuevas, efecto.actualizadas, efecto.sin_cambios) == (0, 1, 0)

    nombre = await sesion.scalar(sa.select(Ticker.nombre).where(Ticker.symbol == "AAPL"))
    assert nombre == "Apple Inc."


async def test_upsert_colapsa_claves_repetidas_del_mismo_lote(sesion: AsyncSession) -> None:
    """Si la fuente manda la misma clave dos veces, gana la ultima y se inserta una sola fila."""
    efecto = await upsert_filas(
        sesion,
        Ticker,
        [
            {"symbol": "AAPL", "nombre": "Viejo"},
            {"symbol": "AAPL", "nombre": "Nuevo"},
        ],
        columnas_clave=("symbol",),
        columnas_actualizar=("nombre",),
    )
    assert efecto.nuevas == 1
    assert await sesion.scalar(sa.select(Ticker.nombre).where(Ticker.symbol == "AAPL")) == "Nuevo"


async def test_upsert_sin_filas_no_hace_nada(sesion: AsyncSession) -> None:
    efecto = await upsert_filas(sesion, Ticker, [], ("symbol",), ("nombre",))
    assert (efecto.nuevas, efecto.actualizadas, efecto.sin_cambios) == (0, 0, 0)


async def test_reintentos_terminan_devolviendo_el_valor() -> None:
    intentos = {"n": 0}

    async def falla_dos_veces() -> str:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise ConnectionError("rate limit")
        return "ok"

    assert await con_reintentos(falla_dos_veces, intentos=4, base_seg=0.001) == "ok"
    assert intentos["n"] == 3


async def test_reintentos_relanzan_el_ultimo_error() -> None:
    async def siempre_falla() -> str:
        raise ConnectionError("caida total")

    try:
        await con_reintentos(siempre_falla, intentos=2, base_seg=0.001)
    except ConnectionError as exc:
        assert "caida total" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("debio relanzar la excepcion")
