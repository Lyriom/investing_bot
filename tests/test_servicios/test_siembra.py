"""Tests de la carga de la whitelist."""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos import Ticker
from investing_bot.servicios.siembra import leer_whitelist, sembrar_whitelist


def test_la_whitelist_semilla_tiene_al_menos_30_instrumentos() -> None:
    """Criterio de aceptacion de la FASE 0: 30 tickers."""
    filas = leer_whitelist()
    assert len(filas) >= 30
    assert len({fila["symbol"] for fila in filas}) == len(filas)


def test_la_whitelist_incluye_spy() -> None:
    """La senal de regimen de mercado (S4) compara SPY contra su MA200."""
    assert "SPY" in {fila["symbol"] for fila in leer_whitelist()}


def test_todos_los_instrumentos_traen_sector() -> None:
    """El gestor de riesgo limita posiciones por sector: sin sector no se puede."""
    assert all(fila["sector"] for fila in leer_whitelist())


async def test_sembrar_carga_los_tickers(sesion: AsyncSession) -> None:
    efecto = await sembrar_whitelist(sesion)
    await sesion.commit()

    total = await sesion.scalar(
        sa.select(sa.func.count()).select_from(Ticker).where(Ticker.en_whitelist.is_(True))
    )
    assert total == efecto.nuevas >= 30


async def test_sembrar_dos_veces_no_duplica(sesion: AsyncSession) -> None:
    await sembrar_whitelist(sesion)
    await sesion.commit()
    primero = await sesion.scalar(sa.select(sa.func.count()).select_from(Ticker))

    segundo_efecto = await sembrar_whitelist(sesion)
    await sesion.commit()
    segundo = await sesion.scalar(sa.select(sa.func.count()).select_from(Ticker))

    assert primero == segundo
    assert segundo_efecto.nuevas == 0


async def test_un_ticker_retirado_sale_de_la_whitelist_sin_perder_historial(
    sesion: AsyncSession, tmp_path: Path
) -> None:
    """El archivo semilla es la fuente de verdad, pero no se borran filas."""
    await sembrar_whitelist(sesion)
    await sesion.commit()

    recortado = tmp_path / "whitelist.json"
    recortado.write_text(
        json.dumps(
            {"version": 1, "tickers": [{"symbol": "SPY", "nombre": "SPDR", "sector": "ETF"}]}
        ),
        encoding="utf-8",
    )
    await sembrar_whitelist(sesion, recortado)
    await sesion.commit()

    en_whitelist = await sesion.scalar(
        sa.select(sa.func.count()).select_from(Ticker).where(Ticker.en_whitelist.is_(True))
    )
    total = await sesion.scalar(sa.select(sa.func.count()).select_from(Ticker))

    assert en_whitelist == 1
    assert total >= 30  # el historial de los retirados sigue ahi
