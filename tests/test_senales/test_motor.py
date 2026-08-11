"""Tests del motor de combinacion."""

from __future__ import annotations

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

from investing_bot.datos.repositorio_pit import RepositorioPIT
from investing_bot.modelos import Senal as SenalModelo
from investing_bot.senales.motor import SCORE_NEUTRO, MotorSenales


async def test_sin_ningun_dato_el_score_es_neutro(sesion: AsyncSession) -> None:
    """Ausencia de evidencia no es evidencia en contra: 50, no 0."""
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, dias=5)
    await sesion.commit()

    resultado = await MotorSenales().calcular(RepositorioPIT(sesion, FECHA), "NVDA", FECHA)

    assert resultado.score == SCORE_NEUTRO
    assert not resultado.hay_alguna_senal


async def test_una_senal_sin_datos_empuja_hacia_lo_neutro(sesion: AsyncSession) -> None:
    """El peso de una senal ausente no se reparte entre las demas.

    Si se repartiera, dos senales podrian fingir la conviccion de tres.
    """
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, variacion_diaria=0.01)
    await sembrar_noticia(sesion, sentimiento=1.0, dias_atras=3)
    await sesion.commit()

    resultado = await MotorSenales().calcular(RepositorioPIT(sesion, FECHA), "NVDA", FECHA)

    # Solo deriva (peso 0.40) sobre un total de 0.80 -> como mucho +25 puntos.
    assert SCORE_NEUTRO < resultado.score <= 75
    assert resultado.hay_alguna_senal


async def test_las_tres_senales_alineadas_dan_un_score_alto(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, dias=40, variacion_diaria=0.01)
    await sembrar_noticia(sesion, sentimiento=0.9, dias_atras=4)
    await sembrar_reddit(sesion, dias=25, menciones_base=10, menciones_hoy=90, sentimiento=0.7)
    await sembrar_congreso(sesion, miembros=4, tipo="compra")
    await sesion.commit()

    resultado = await MotorSenales().calcular(RepositorioPIT(sesion, FECHA), "NVDA", FECHA)

    assert resultado.score > 75
    assert all(c.datos_suficientes for c in resultado.componentes)


async def test_el_regimen_de_riesgo_recorta_el_score(sesion: AsyncSession) -> None:
    """El regimen puede bajar un score, nunca subirlo."""
    await sembrar_ticker(sesion)
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, dias=40, variacion_diaria=0.01)
    await sembrar_precios(sesion, symbol="SPY", dias=260, variacion_diaria=-0.002)
    await sembrar_noticia(sesion, sentimiento=0.9, dias_atras=4)
    await sesion.commit()

    resultado = await MotorSenales().calcular(RepositorioPIT(sesion, FECHA), "NVDA", FECHA)

    assert resultado.regimen.modo_defensivo
    assert resultado.score < resultado.score_antes_regimen


async def test_el_regimen_no_sube_un_score_por_debajo_de_neutro(
    sesion: AsyncSession,
) -> None:
    await sembrar_ticker(sesion)
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, dias=40, variacion_diaria=-0.01)
    await sembrar_precios(sesion, symbol="SPY", dias=260, variacion_diaria=-0.002)
    await sembrar_noticia(sesion, sentimiento=-0.9, dias_atras=4)
    await sesion.commit()

    resultado = await MotorSenales().calcular(RepositorioPIT(sesion, FECHA), "NVDA", FECHA)

    assert resultado.score < SCORE_NEUTRO
    assert resultado.score == resultado.score_antes_regimen


async def test_el_score_queda_siempre_entre_0_y_100(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, dias=40, variacion_diaria=0.05)
    await sembrar_noticia(sesion, sentimiento=1.0, dias_atras=2)
    await sembrar_reddit(sesion, dias=25, menciones_base=1, menciones_hoy=5000, sentimiento=1.0)
    await sembrar_congreso(sesion, miembros=20, tipo="compra")
    await sesion.commit()

    resultado = await MotorSenales().calcular(RepositorioPIT(sesion, FECHA), "NVDA", FECHA)
    assert 0 <= resultado.score <= 100


async def test_los_componentes_quedan_guardados_para_auditar(
    sesion: AsyncSession,
) -> None:
    """Invariante I3: sin desglose ni version del modelo, no es auditable."""
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, dias=40, variacion_diaria=0.01)
    await sembrar_noticia(sesion, sentimiento=0.9, dias_atras=4)
    await sesion.commit()

    motor = MotorSenales()
    resultados = await motor.calcular_whitelist(sesion, FECHA)
    await motor.persistir(sesion, resultados)

    fila = await sesion.scalar(sa.select(SenalModelo).where(SenalModelo.symbol == "NVDA"))
    assert fila is not None
    assert fila.version_modelo
    assert set(fila.componentes) >= {"deriva", "reddit", "congreso", "regimen"}
    assert "puntos" in fila.componentes["deriva"]
    assert fila.componentes["deriva"]["detalle"]["noticias"] == 1


async def test_persistir_dos_veces_no_duplica(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, dias=10)
    await sesion.commit()

    motor = MotorSenales()
    resultados = await motor.calcular_whitelist(sesion, FECHA)
    await motor.persistir(sesion, resultados)
    await motor.persistir(sesion, resultados)

    total = await sesion.scalar(sa.select(sa.func.count()).select_from(SenalModelo))
    assert total == 1


async def test_los_resultados_vienen_ordenados_por_score(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion, symbol="NVDA")
    await sembrar_ticker(sesion, symbol="AMD")
    await sembrar_precios(sesion, symbol="NVDA", dias=40, variacion_diaria=0.01)
    await sembrar_precios(sesion, symbol="AMD", dias=40, variacion_diaria=-0.01)
    await sembrar_noticia(sesion, symbol="NVDA", sentimiento=0.9, dias_atras=4)
    await sembrar_noticia(sesion, symbol="AMD", sentimiento=-0.9, dias_atras=4)
    await sesion.commit()

    resultados = await MotorSenales().calcular_whitelist(sesion, FECHA)

    assert [r.symbol for r in resultados] == ["NVDA", "AMD"]
    assert resultados[0].score > resultados[1].score
