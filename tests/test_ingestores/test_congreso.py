"""Tests del ingestor de Congreso. Datos sinteticos: nunca se golpea la red."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.db import ahora_utc
from investing_bot.ingestores.congreso import (
    URL_CAMARA,
    IngestorCongreso,
    normalizar_operacion,
    normalizar_tipo,
    parsear_fecha,
    parsear_rango_monto,
)
from investing_bot.modelos import CongresoTrade, Ticker

HOY = ahora_utc().date()


def _crudo(**extra: Any) -> dict[str, Any]:
    base = {
        "representative": "Hon. Alguien",
        "ticker": "NVDA",
        "type": "purchase",
        "amount": "$1,001 - $15,000",
        "transaction_date": (HOY - timedelta(days=60)).isoformat(),
        "disclosure_date": (HOY - timedelta(days=20)).isoformat(),
        "asset_description": "NVIDIA Corporation Common Stock",
        "ptr_link": "https://example.test/filing",
    }
    base.update(extra)
    return base


# --- Parseo ----------------------------------------------------------------


def test_normalizar_tipo_traduce_las_variantes_de_las_dos_camaras() -> None:
    assert normalizar_tipo("purchase") == "compra"
    assert normalizar_tipo("Purchase") == "compra"
    assert normalizar_tipo("sale_full") == "venta"
    assert normalizar_tipo("Sale (Partial)") == "venta"
    assert normalizar_tipo("exchange") == "intercambio"


def test_un_tipo_desconocido_se_descarta_en_vez_de_adivinarse() -> None:
    """Registrar una operacion rara como compra seria peor que perderla."""
    assert normalizar_tipo("something weird") is None
    assert normalizar_tipo(None) is None


def test_parsear_rango_de_monto() -> None:
    assert parsear_rango_monto("$1,001 - $15,000") == (Decimal("1001"), Decimal("15000"))
    assert parsear_rango_monto("$1,000,001 - $5,000,000") == (
        Decimal("1000001"),
        Decimal("5000000"),
    )
    assert parsear_rango_monto(None) == (None, None)


def test_parsear_fecha_acepta_los_formatos_de_las_dos_fuentes() -> None:
    assert parsear_fecha("2026-03-01") == date(2026, 3, 1)
    assert parsear_fecha("03/01/2026") == date(2026, 3, 1)
    assert parsear_fecha("basura") is None


# --- Normalizacion de una operacion ---------------------------------------


def test_se_calcula_el_retraso_del_disclosure() -> None:
    fila = normalizar_operacion(
        _crudo(transaction_date="2026-03-01", disclosure_date="2026-04-14"),
        "camara",
        {"NVDA"},
        ahora_utc(),
    )
    assert fila is not None
    assert fila["dias_retraso"] == 44
    assert fila["presentacion_tardia"] is False


def test_se_marca_la_presentacion_tardia() -> None:
    """La ley concede 45 dias; pasado eso, la presentacion es tardia."""
    fila = normalizar_operacion(
        _crudo(transaction_date="2026-03-01", disclosure_date="2026-06-01"),
        "camara",
        {"NVDA"},
        ahora_utc(),
    )
    assert fila is not None
    assert fila["presentacion_tardia"] is True


def test_las_tres_fechas_quedan_separadas() -> None:
    """Confundirlas es el bug mas caro del dominio (invariante I1)."""
    observado = ahora_utc()
    fila = normalizar_operacion(
        _crudo(transaction_date="2026-03-01", disclosure_date="2026-04-14"),
        "camara",
        {"NVDA"},
        observado,
    )
    assert fila is not None
    assert fila["fecha_transaccion"] == date(2026, 3, 1)
    assert fila["fecha_disclosure"] == date(2026, 4, 14)
    assert fila["observed_at"] == observado


def test_un_ticker_fuera_de_la_base_no_rompe_la_clave_foranea() -> None:
    fila = normalizar_operacion(_crudo(ticker="ZZZZ"), "camara", {"NVDA"}, ahora_utc())
    assert fila is not None
    assert fila["symbol"] is None
    assert fila["descripcion_activo"]  # el dato no se pierde


def test_se_descartan_las_filas_sin_miembro_o_sin_fecha() -> None:
    assert normalizar_operacion(_crudo(representative=""), "camara", set(), ahora_utc()) is None
    assert (
        normalizar_operacion(_crudo(transaction_date="basura"), "camara", set(), ahora_utc())
        is None
    )


def test_el_senador_se_lee_del_campo_correcto() -> None:
    bruto = _crudo()
    del bruto["representative"]
    bruto["senator"] = "Sen. Otra Persona"
    fila = normalizar_operacion(bruto, "senado", {"NVDA"}, ahora_utc())
    assert fila is not None
    assert fila["miembro"] == "Sen. Otra Persona"


# --- Ingestor completo -----------------------------------------------------


async def _sembrar(sesion: AsyncSession) -> None:
    sesion.add(Ticker(symbol="NVDA", nombre="NVIDIA Corporation", en_whitelist=True))
    await sesion.commit()


def _descargador(datos: Sequence[dict[str, Any]]) -> Any:
    async def descargar(url: str) -> list[dict[str, Any]]:
        return list(datos) if url == URL_CAMARA else []

    return descargar


async def test_ingesta_guarda_los_trades(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await _sembrar(sesion)
    ingestor = IngestorCongreso(fabrica_sesiones=fabrica, descargador=_descargador([_crudo()]))

    resultado = await ingestor.ejecutar()

    assert resultado.exito
    assert resultado.filas_nuevas == 1
    trade = await sesion.scalar(sa.select(CongresoTrade))
    assert trade is not None
    assert trade.symbol == "NVDA"
    assert trade.tipo == "compra"


async def test_ingesta_de_congreso_es_idempotente(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await _sembrar(sesion)
    ingestor = IngestorCongreso(fabrica_sesiones=fabrica, descargador=_descargador([_crudo()]))

    await ingestor.ejecutar()
    segundo = await ingestor.ejecutar()

    assert segundo.filas_nuevas == 0
    total = await sesion.scalar(sa.select(sa.func.count()).select_from(CongresoTrade))
    assert total == 1


async def test_se_descartan_los_trades_fuera_de_la_ventana(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await _sembrar(sesion)
    viejo = _crudo(transaction_date=(HOY - timedelta(days=400)).isoformat())
    ingestor = IngestorCongreso(
        fabrica_sesiones=fabrica, dias=180, descargador=_descargador([viejo])
    )

    resultado = await ingestor.ejecutar()
    assert resultado.filas_nuevas == 0


async def test_si_una_camara_falla_la_otra_sigue(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    """SPEC 6.1: si una fuente cae, se registra el error y las demas siguen."""
    await _sembrar(sesion)

    async def descargar(url: str) -> list[dict[str, Any]]:
        if url == URL_CAMARA:
            return [_crudo()]
        raise ConnectionError("el senado se cayo")

    ingestor = IngestorCongreso(fabrica_sesiones=fabrica, descargador=descargar)
    ingestor.reintentos = 1
    resultado = await ingestor.ejecutar()

    assert resultado.exito
    assert resultado.filas_nuevas == 1
    assert any("senado" in e for e in resultado.errores)
