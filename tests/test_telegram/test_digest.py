"""Tests del digest diario: el pipeline completo, de los datos al mensaje."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_senales.conftest import (
    FECHA,
    sembrar_congreso,
    sembrar_noticia,
    sembrar_precios,
    sembrar_reddit,
    sembrar_ticker,
)

from investing_bot.config import limpiar_cache_config
from investing_bot.modelos import PosicionSombra, Sugerencia
from investing_bot.servicios.digest import (
    estan_pausados,
    fijar_pausa,
    generar_digest,
    persistir_digest,
)
from investing_bot.telegram.digest import ADVERTENCIA_SIN_BACKTEST


@pytest.fixture(autouse=True)
def capital_viable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capital con el que el sistema puede operar.

    Con los 150 USD del SPEC, el 25 % maximo por posicion son 37,50 — por
    debajo del minimo viable de 50 — y el gestor veta absolutamente todo.
    Ver `test_con_capital_150_el_sistema_no_puede_sugerir_nada`.
    """
    monkeypatch.setenv("CAPITAL_TOTAL_USD", "500")
    limpiar_cache_config()


async def _escenario_con_senal_fuerte(sesion: AsyncSession) -> None:
    """Un ticker con las tres senales alineadas y regimen alcista."""
    await sembrar_ticker(sesion, symbol="NVDA")
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, symbol="NVDA", dias=40, variacion_diaria=0.01)
    await sembrar_precios(sesion, symbol="SPY", dias=260, variacion_diaria=0.002)
    await sembrar_noticia(sesion, symbol="NVDA", sentimiento=0.9, dias_atras=4)
    await sembrar_reddit(
        sesion, symbol="NVDA", dias=25, menciones_base=10, menciones_hoy=90, sentimiento=0.7
    )
    await sembrar_congreso(sesion, symbol="NVDA", miembros=4, tipo="compra")
    await sesion.commit()


# --- Contenido del mensaje -------------------------------------------------


async def test_el_digest_incluye_desglose_stop_y_tamano(sesion: AsyncSession) -> None:
    """Invariante I3: sin las tres cosas, la sugerencia no es auditable."""
    await _escenario_con_senal_fuerte(sesion)

    resultado = await generar_digest(sesion, FECHA)

    assert resultado.decision.propuestas, "esperaba al menos una sugerencia"
    texto = resultado.texto
    assert "NVDA" in texto
    assert "Deriva post-noticia" in texto
    assert "Velocidad Reddit" in texto
    assert "Consenso Congreso" in texto
    assert "Regimen" in texto
    assert "Stop" in texto
    assert "Tamano" in texto
    assert "Costo est." in texto
    assert "/desglose NVDA" in texto


async def test_el_digest_siempre_advierte_que_no_hubo_backtest(
    sesion: AsyncSession,
) -> None:
    """Mientras la FASE 2 no exista, el mensaje tiene que decirlo."""
    await _escenario_con_senal_fuerte(sesion)

    resultado = await generar_digest(sesion, FECHA)
    assert ADVERTENCIA_SIN_BACKTEST in resultado.texto
    assert "NO pasaron por el backtester" in resultado.texto


async def test_el_modo_defensivo_se_anuncia_y_bloquea_las_compras(
    sesion: AsyncSession,
) -> None:
    """Probado con una serie bajista, como pide el criterio de la FASE 3."""
    await sembrar_ticker(sesion, symbol="NVDA")
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, symbol="NVDA", dias=40, variacion_diaria=0.01)
    await sembrar_precios(sesion, symbol="SPY", dias=260, variacion_diaria=-0.002)
    await sembrar_noticia(sesion, symbol="NVDA", sentimiento=0.9, dias_atras=4)
    await sembrar_reddit(
        sesion, symbol="NVDA", dias=25, menciones_base=10, menciones_hoy=90, sentimiento=0.7
    )
    await sembrar_congreso(sesion, symbol="NVDA", miembros=4, tipo="compra")
    await sesion.commit()

    resultado = await generar_digest(sesion, FECHA)

    assert resultado.decision.modo_defensivo
    assert "Modo defensivo: sin compras nuevas" in resultado.texto
    assert "MA200" in resultado.texto
    assert resultado.decision.propuestas == []


async def test_un_dia_sin_sugerencias_explica_por_que(sesion: AsyncSession) -> None:
    """Un digest vacio no dice nada util. Este dice que paso."""
    await sembrar_ticker(sesion, symbol="NVDA")
    await sembrar_precios(sesion, symbol="NVDA", dias=10)
    await sesion.commit()

    resultado = await generar_digest(sesion, FECHA)

    assert "Sin sugerencias hoy" in resultado.texto
    assert "Ninguna senal tuvo datos suficientes" in resultado.texto


# --- Persistencia ----------------------------------------------------------


async def test_persistir_guarda_sugerencia_y_posicion_sombra(
    sesion: AsyncSession,
) -> None:
    await _escenario_con_senal_fuerte(sesion)

    resultado = await generar_digest(sesion, FECHA)
    guardadas = await persistir_digest(sesion, resultado)

    assert guardadas == len(resultado.decision.propuestas) >= 1

    sugerencia = await sesion.scalar(sa.select(Sugerencia))
    assert sugerencia is not None
    assert sugerencia.senal_id is not None, "la sugerencia debe atarse a su senal"
    assert sugerencia.stop_sugerido is not None
    assert sugerencia.razon

    posicion = await sesion.scalar(sa.select(PosicionSombra))
    assert posicion is not None
    assert posicion.abierta is True


async def test_una_posicion_abierta_bloquea_una_segunda_sugerencia(
    sesion: AsyncSession,
) -> None:
    """El gestor de riesgo lee el portafolio sombra para no doblar exposicion."""
    await _escenario_con_senal_fuerte(sesion)

    primero = await generar_digest(sesion, FECHA)
    await persistir_digest(sesion, primero)

    segundo = await generar_digest(sesion, FECHA)

    assert segundo.decision.propuestas == []
    assert any(v.regla == "posicion_abierta" for v in segundo.decision.vetos)


# --- Kill switch -----------------------------------------------------------


async def test_el_kill_switch_se_persiste(sesion: AsyncSession) -> None:
    assert not await estan_pausados(sesion)

    await fijar_pausa(sesion, True)
    assert await estan_pausados(sesion)

    await fijar_pausa(sesion, False)
    assert not await estan_pausados(sesion)


async def test_con_capital_150_el_sistema_no_puede_sugerir_nada(
    sesion: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los parametros por defecto del SPEC son mutuamente incompatibles.

    `MAX_PCT_POR_POSICION = 0.25` y `MIN_TAMANO_POSICION_USD = 50` implican un
    capital minimo de 200 USD. Con los 100-150 que el SPEC propone para
    empezar, toda sugerencia queda vetada. El sistema lo dice en vez de
    inflar la posicion y romper su propio limite de concentracion.
    """
    monkeypatch.setenv("CAPITAL_TOTAL_USD", "150")
    limpiar_cache_config()
    await _escenario_con_senal_fuerte(sesion)

    resultado = await generar_digest(sesion, FECHA)

    assert resultado.decision.propuestas == []
    veto = next(v for v in resultado.decision.vetos if v.regla == "tamano_minimo")
    assert "$37.50" in veto.motivo
    assert "no llega al minimo viable de $50" in veto.motivo
