"""Tests de las consultas que alimentan el dashboard y `/estado`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos import CorridaIngesta, PrecioDiario, Ticker
from investing_bot.servicios.consultas import (
    INGESTORES_ESPERADOS,
    conteos_generales,
    estado_ingestores,
    resumen_tickers,
    ultimos_precios,
)

AHORA = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)


async def test_sin_corridas_todos_los_ingestores_aparecen(sesion: AsyncSession) -> None:
    """El dashboard lista los cuatro ingestores del SPEC."""
    estados = await estado_ingestores(sesion)
    assert [e.nombre for e in estados] == list(INGESTORES_ESPERADOS)
    assert all(e.implementado for e in estados)
    assert all(e.ultima_corrida is None for e in estados)


async def test_se_reporta_la_corrida_mas_reciente(sesion: AsyncSession) -> None:
    sesion.add(
        CorridaIngesta(
            ingestor="precios",
            iniciado_at=AHORA - timedelta(days=1),
            exito=False,
            filas_nuevas=0,
            errores={"mensajes": ["la fuente se cayo"]},
        )
    )
    sesion.add(
        CorridaIngesta(
            ingestor="precios",
            iniciado_at=AHORA,
            exito=True,
            filas_nuevas=2700,
            filas_actualizadas=3,
            duracion_seg=4.2,
        )
    )
    await sesion.commit()

    precios = next(e for e in await estado_ingestores(sesion) if e.nombre == "precios")
    assert precios.exito is True
    assert precios.filas_nuevas == 2700
    assert precios.duracion_seg == 4.2
    assert precios.errores is None


async def test_los_errores_de_la_ultima_corrida_se_propagan(sesion: AsyncSession) -> None:
    sesion.add(
        CorridaIngesta(
            ingestor="precios",
            iniciado_at=AHORA,
            exito=False,
            errores={"mensajes": ["429 rate limit", "timeout"]},
        )
    )
    await sesion.commit()

    precios = next(e for e in await estado_ingestores(sesion) if e.nombre == "precios")
    assert precios.errores == ["429 rate limit", "timeout"]


async def test_resumen_tickers_incluye_los_que_no_tienen_precios(sesion: AsyncSession) -> None:
    """Un ticker sin barras debe verse como hueco de cobertura, no desaparecer."""
    sesion.add(Ticker(symbol="AAPL", nombre="Apple Inc.", sector="Tecnologia", en_whitelist=True))
    sesion.add(Ticker(symbol="MSFT", nombre="Microsoft", sector="Tecnologia", en_whitelist=True))
    sesion.add(
        PrecioDiario(
            symbol="AAPL",
            fecha=date(2026, 8, 10),
            cierre=Decimal("178.40"),
            observed_at=AHORA,
        )
    )
    await sesion.commit()

    resumen = {r.symbol: r for r in await resumen_tickers(sesion)}
    assert resumen["AAPL"].barras == 1
    assert resumen["AAPL"].primera_fecha == date(2026, 8, 10)
    assert resumen["MSFT"].barras == 0
    assert resumen["MSFT"].ultima_fecha is None


async def test_resumen_tickers_excluye_los_que_salieron_de_la_whitelist(
    sesion: AsyncSession,
) -> None:
    sesion.add(Ticker(symbol="AAPL", en_whitelist=True))
    sesion.add(Ticker(symbol="RETIRADO", en_whitelist=False))
    await sesion.commit()

    assert {r.symbol for r in await resumen_tickers(sesion)} == {"AAPL"}


async def test_conteos_generales(sesion: AsyncSession) -> None:
    sesion.add(Ticker(symbol="AAPL", en_whitelist=True))
    sesion.add(Ticker(symbol="RETIRADO", en_whitelist=False))
    sesion.add(
        PrecioDiario(symbol="AAPL", fecha=date(2026, 8, 10), cierre=Decimal("1"), observed_at=AHORA)
    )
    await sesion.commit()

    conteos = await conteos_generales(sesion)
    assert conteos["tickers_whitelist"] == 1
    assert conteos["barras_precio"] == 1
    assert conteos["ejecuciones"] == 0
    # Las fuentes de la FASE 1 tambien se cuentan, aunque esten vacias.
    assert {"noticias", "noticias_duplicadas", "reddit", "congreso"} <= set(conteos)


async def test_ultimos_precios_vienen_del_mas_reciente_al_mas_antiguo(
    sesion: AsyncSession,
) -> None:
    sesion.add(Ticker(symbol="AAPL", en_whitelist=True))
    for dia in (8, 9, 10):
        sesion.add(
            PrecioDiario(
                symbol="AAPL",
                fecha=date(2026, 8, dia),
                cierre=Decimal(dia),
                observed_at=AHORA,
            )
        )
    await sesion.commit()

    barras = await ultimos_precios(sesion, "aapl", limite=2)
    assert [b.fecha.day for b in barras] == [10, 9]
