"""Tests del esquema: restricciones que protegen los invariantes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.db import Base
from investing_bot.modelos import CongresoTrade, Noticia, PrecioDiario, Ticker


def test_todas_las_tablas_del_spec_existen() -> None:
    """El esquema completo de la seccion 5 del SPEC esta declarado."""
    esperadas = {
        "tickers",
        "precios_diarios",
        "noticias",
        "reddit_diario",
        "congreso_trades",
        "senales",
        "sugerencias",
        "ejecuciones",
        "posiciones_sombra",
        "corridas_ingesta",
        "estado_sistema",
    }
    assert esperadas <= set(Base.metadata.tables)


@pytest.mark.parametrize(
    "tabla",
    ["precios_diarios", "noticias", "reddit_diario", "congreso_trades"],
)
def test_las_tablas_de_datos_externos_tienen_observed_at(tabla: str) -> None:
    """Invariante I1: toda tabla de datos externos lleva `observed_at`."""
    columnas = Base.metadata.tables[tabla].columns
    assert "observed_at" in columnas
    assert not columnas["observed_at"].nullable


def test_congreso_separa_las_tres_fechas() -> None:
    """El bug mas caro del dominio se previene teniendo las tres por separado."""
    columnas = Base.metadata.tables["congreso_trades"].columns
    assert {"fecha_transaccion", "fecha_disclosure", "observed_at"} <= set(columnas.keys())


async def test_no_se_puede_duplicar_una_barra(sesion: AsyncSession) -> None:
    """UNIQUE(symbol, fecha) es lo que hace idempotente al ingestor de precios."""
    sesion.add(Ticker(symbol="AAPL", nombre="Apple Inc.", en_whitelist=True))
    await sesion.flush()

    comun = {
        "symbol": "AAPL",
        "fecha": date(2026, 8, 10),
        "cierre": Decimal("178.40"),
        "observed_at": datetime(2026, 8, 10, 21, 0, tzinfo=UTC),
    }
    sesion.add(PrecioDiario(**comun))
    await sesion.flush()

    sesion.add(PrecioDiario(**comun))
    with pytest.raises(IntegrityError):
        await sesion.flush()


async def test_no_se_puede_duplicar_una_noticia_por_hash(sesion: AsyncSession) -> None:
    """UNIQUE(hash_contenido): diez portales replicando un cable son un dato, no diez."""
    comun = {
        "titulo": "Resultados por encima de lo esperado",
        "hash_contenido": "a" * 64,
        "event_at": datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        "observed_at": datetime(2026, 8, 10, 12, 5, tzinfo=UTC),
    }
    sesion.add(Noticia(**comun))
    await sesion.flush()

    sesion.add(Noticia(**comun))
    with pytest.raises(IntegrityError):
        await sesion.flush()


async def test_no_se_puede_duplicar_un_trade_del_congreso(sesion: AsyncSession) -> None:
    sesion.add(Ticker(symbol="NVDA", en_whitelist=True))
    await sesion.flush()

    comun = {
        "miembro": "Un miembro cualquiera",
        "symbol": "NVDA",
        "tipo": "compra",
        "monto_min": Decimal("1000"),
        "fecha_transaccion": date(2026, 3, 1),
        "observed_at": datetime(2026, 4, 14, 12, 0, tzinfo=UTC),
    }
    sesion.add(CongresoTrade(**comun))
    await sesion.flush()

    sesion.add(CongresoTrade(**comun))
    with pytest.raises(IntegrityError):
        await sesion.flush()


def test_el_unique_del_congreso_trata_los_nulos_como_iguales() -> None:
    """Muchos trades llegan sin ticker resuelto (`symbol` nulo).

    Con la semantica SQL por defecto NULL != NULL, de modo que el UNIQUE no
    dispararia y el ingestor perderia la idempotencia justo en esas filas.
    `NULLS NOT DISTINCT` (PostgreSQL 15+) lo corrige. Como sqlite no lo
    soporta, aqui se verifica que la restriccion este declarada asi.
    """
    restriccion = next(
        c
        for c in Base.metadata.tables["congreso_trades"].constraints
        if c.name == "uq_congreso_trade"
    )
    assert restriccion.dialect_kwargs.get("postgresql_nulls_not_distinct") is True


async def test_ejecucion_admite_sugerencia_nula(sesion: AsyncSession) -> None:
    """A proposito: permite registrar operaciones tomadas sin senal del sistema."""
    columnas = Base.metadata.tables["ejecuciones"].columns
    assert columnas["sugerencia_id"].nullable
