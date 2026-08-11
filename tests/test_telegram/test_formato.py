"""Tests del formateo de mensajes."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from investing_bot.servicios.consultas import EstadoIngestor
from investing_bot.telegram.formato import (
    formatear_bienvenida,
    formatear_estado,
    formatear_fecha_corta,
)


def test_la_bienvenida_deja_claro_que_no_se_ejecutan_ordenes() -> None:
    """Invariante I2, visible para el operador en el primer mensaje."""
    texto = formatear_bienvenida(42)
    assert "NO ejecuta ordenes" in texto
    assert "42" in texto


def test_la_fecha_se_muestra_en_la_zona_del_operador() -> None:
    momento = datetime(2026, 8, 11, 22, 0, tzinfo=UTC)  # 17:00 en Ecuador (UTC-5)
    assert formatear_fecha_corta(momento, "America/Guayaquil") == "11 ago 17:00"


def test_sin_corridas_la_fecha_dice_nunca() -> None:
    assert formatear_fecha_corta(None, "America/Guayaquil") == "nunca"


def test_el_estado_distingue_pendiente_de_fallo() -> None:
    texto = formatear_estado(
        entorno="desarrollo",
        capital_usd=Decimal("150"),
        conteos={"tickers_whitelist": 30, "barras_precio": 1860, "ejecuciones": 0},
        ingestores=[
            EstadoIngestor(
                nombre="precios",
                implementado=True,
                ultima_corrida=datetime(2026, 8, 11, 22, 0, tzinfo=UTC),
                exito=True,
                filas_nuevas=30,
            ),
            EstadoIngestor(nombre="noticias", implementado=False),
            EstadoIngestor(
                nombre="reddit",
                implementado=True,
                ultima_corrida=datetime(2026, 8, 11, 22, 0, tzinfo=UTC),
                exito=False,
                errores=["429 rate limit"],
            ),
        ],
        zona_operador="America/Guayaquil",
    )
    assert "1860" in texto
    assert "precios" in texto and "ok" in texto
    assert "pendiente (FASE 1)" in texto
    assert "FALLO" in texto
    assert "429 rate limit" in texto


def test_el_estado_no_finge_tener_portafolio_en_fase_0() -> None:
    """Mostrar un portafolio vacio se leeria como informacion real. Se dice la verdad."""
    texto = formatear_estado(
        entorno="desarrollo",
        capital_usd=Decimal("150"),
        conteos={},
        ingestores=[],
        zona_operador="America/Guayaquil",
    )
    assert "no disponibles en FASE 0" in texto
