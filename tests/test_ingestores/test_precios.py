"""Tests del ingestor de precios. Datos sinteticos: nunca se golpea la red."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.ingestores.precios import (
    IngestorPrecios,
    aplanar_frame_yfinance,
    momento_observacion,
)
from investing_bot.modelos import CorridaIngesta, PrecioDiario, Ticker

ZONA = "America/New_York"
CAMPOS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _frame_sintetico(con_hueco: bool = False) -> pd.DataFrame:
    """DataFrame con el mismo formato que devuelve yfinance para varios tickers."""
    indice = pd.to_datetime(["2026-08-05", "2026-08-06", "2026-08-07"])
    columnas = pd.MultiIndex.from_product([["AAPL", "MSFT"], CAMPOS])
    datos = [
        [
            180.0,
            182.0,
            179.0,
            181.0,
            181.0,
            50_000_000,
            400.0,
            404.0,
            399.0,
            402.0,
            402.0,
            20_000_000,
        ],
        [
            181.5,
            184.0,
            181.0,
            183.5,
            183.5,
            52_000_000,
            402.5,
            407.0,
            402.0,
            406.0,
            406.0,
            21_000_000,
        ],
        [
            183.0,
            186.0,
            182.5,
            185.2,
            185.2,
            55_000_000,
            406.5,
            409.0,
            405.0,
            408.4,
            408.4,
            19_500_000,
        ],
    ]
    frame = pd.DataFrame(datos, index=indice, columns=columnas)
    if con_hueco:
        # Dia sin datos para MSFT: la barra debe descartarse, no inventarse.
        frame.loc[indice[1], ("MSFT", "Close")] = float("nan")
    return frame


async def _sembrar_tickers(sesion: AsyncSession, symbols: Sequence[str]) -> None:
    for symbol in symbols:
        sesion.add(Ticker(symbol=symbol, nombre=symbol, en_whitelist=True, activo=True))
    await sesion.commit()


# --- momento_observacion ---------------------------------------------------


def test_observed_at_es_el_cierre_mas_una_hora_en_invierno() -> None:
    """En EST (UTC-5): cierre 16:00 ET = 21:00 UTC, mas 1 h = 22:00 UTC."""
    assert momento_observacion(date(2026, 1, 15), ZONA) == datetime(2026, 1, 15, 22, 0, tzinfo=UTC)


def test_observed_at_respeta_el_horario_de_verano() -> None:
    """En EDT (UTC-4): cierre 16:00 ET = 20:00 UTC, mas 1 h = 21:00 UTC."""
    assert momento_observacion(date(2026, 7, 15), ZONA) == datetime(2026, 7, 15, 21, 0, tzinfo=UTC)


def test_observed_at_nunca_es_anterior_al_dia_de_la_barra() -> None:
    fecha = date(2026, 8, 7)
    assert momento_observacion(fecha, ZONA).date() >= fecha


# --- aplanar_frame_yfinance ------------------------------------------------


def test_aplanar_produce_una_fila_por_ticker_y_dia() -> None:
    filas = aplanar_frame_yfinance(_frame_sintetico(), ["AAPL", "MSFT"], ZONA)
    assert len(filas) == 6
    aapl = [f for f in filas if f["symbol"] == "AAPL"]
    assert len(aapl) == 3
    assert aapl[0]["fecha"] == date(2026, 8, 5)
    assert float(aapl[0]["cierre"]) == 181.0
    assert aapl[0]["volumen"] == 50_000_000
    assert aapl[0]["observed_at"] == momento_observacion(date(2026, 8, 5), ZONA)


def test_aplanar_descarta_las_barras_sin_cierre() -> None:
    """Un dia sin datos se pierde; nunca se rellena con un precio inventado."""
    filas = aplanar_frame_yfinance(_frame_sintetico(con_hueco=True), ["AAPL", "MSFT"], ZONA)
    msft = [f for f in filas if f["symbol"] == "MSFT"]
    assert len(msft) == 2
    assert date(2026, 8, 6) not in [f["fecha"] for f in msft]


def test_aplanar_ignora_symbols_no_pedidos() -> None:
    filas = aplanar_frame_yfinance(_frame_sintetico(), ["AAPL"], ZONA)
    assert {f["symbol"] for f in filas} == {"AAPL"}


def test_aplanar_acepta_columnas_planas_con_un_solo_symbol() -> None:
    plano = _frame_sintetico()["AAPL"]
    filas = aplanar_frame_yfinance(plano, ["AAPL"], ZONA)
    assert len(filas) == 3
    assert {f["symbol"] for f in filas} == {"AAPL"}


def test_aplanar_frame_vacio_devuelve_lista_vacia() -> None:
    assert aplanar_frame_yfinance(pd.DataFrame(), ["AAPL"], ZONA) == []


# --- IngestorPrecios -------------------------------------------------------


async def test_ingesta_guarda_las_barras(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await _sembrar_tickers(sesion, ["AAPL", "MSFT"])

    ingestor = IngestorPrecios(
        fabrica_sesiones=fabrica,
        dias=10,
        descargador=lambda symbols, dias: _frame_sintetico(),
    )
    resultado = await ingestor.ejecutar()

    assert resultado.exito
    assert resultado.filas_nuevas == 6
    total = await sesion.scalar(sa.select(sa.func.count()).select_from(PrecioDiario))
    assert total == 6


async def test_ingesta_es_idempotente(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    """SPEC 6.1: correr dos veces el mismo dia no duplica filas."""
    await _sembrar_tickers(sesion, ["AAPL", "MSFT"])
    ingestor = IngestorPrecios(
        fabrica_sesiones=fabrica,
        dias=10,
        descargador=lambda symbols, dias: _frame_sintetico(),
    )

    await ingestor.ejecutar()
    segundo = await ingestor.ejecutar()

    assert segundo.filas_nuevas == 0
    assert segundo.filas_sin_cambios == 6
    total = await sesion.scalar(sa.select(sa.func.count()).select_from(PrecioDiario))
    assert total == 6


async def test_ingesta_actualiza_metadatos_del_ticker(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await _sembrar_tickers(sesion, ["AAPL"])
    ingestor = IngestorPrecios(
        fabrica_sesiones=fabrica,
        dias=10,
        symbols=["AAPL"],
        descargador=lambda symbols, dias: _frame_sintetico(),
    )
    await ingestor.ejecutar()

    ticker = await sesion.get(Ticker, "AAPL")
    assert ticker is not None
    await sesion.refresh(ticker)
    assert ticker.precio_ultimo is not None
    assert float(ticker.precio_ultimo) == 185.2  # cierre del dia mas reciente
    assert ticker.volumen_promedio_30d is not None


async def test_ingesta_sin_whitelist_falla_con_mensaje_claro(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    ingestor = IngestorPrecios(
        fabrica_sesiones=fabrica,
        dias=10,
        descargador=lambda symbols, dias: _frame_sintetico(),
    )
    resultado = await ingestor.ejecutar()

    assert not resultado.exito
    assert "whitelist" in resultado.errores[0]


async def test_un_fallo_de_la_fuente_no_tumba_el_pipeline(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    """SPEC 6.1: nunca una excepcion de un ingestor propaga hacia arriba."""
    await _sembrar_tickers(sesion, ["AAPL"])

    def descargador_roto(symbols: Sequence[str], dias: int) -> pd.DataFrame:
        raise ConnectionError("la fuente se cayo")

    ingestor = IngestorPrecios(
        fabrica_sesiones=fabrica,
        dias=10,
        descargador=descargador_roto,
        reintentos=2,
        backoff_base_seg=0.001,
    )
    resultado = await ingestor.ejecutar_registrado()

    assert not resultado.exito
    assert "ConnectionError" in resultado.errores[0]

    corrida = await sesion.scalar(
        sa.select(CorridaIngesta).order_by(CorridaIngesta.id.desc()).limit(1)
    )
    assert corrida is not None
    assert corrida.ingestor == "precios"
    assert corrida.exito is False


async def test_la_corrida_exitosa_queda_registrada(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await _sembrar_tickers(sesion, ["AAPL"])
    ingestor = IngestorPrecios(
        fabrica_sesiones=fabrica,
        dias=10,
        symbols=["AAPL"],
        descargador=lambda symbols, dias: _frame_sintetico(),
    )
    await ingestor.ejecutar_registrado()

    corrida = await sesion.scalar(
        sa.select(CorridaIngesta).order_by(CorridaIngesta.id.desc()).limit(1)
    )
    assert corrida is not None
    assert corrida.exito is True
    assert corrida.filas_nuevas == 3
    assert corrida.duracion_seg is not None
