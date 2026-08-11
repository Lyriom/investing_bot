"""Tests de las cuatro senales."""

from __future__ import annotations

from datetime import date, timedelta

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
from investing_bot.senales.consenso_congreso import ConsensoCongreso
from investing_bot.senales.deriva_noticias import DerivaNoticias
from investing_bot.senales.regimen import (
    REGIMEN_ALCISTA,
    REGIMEN_DESCONOCIDO,
    REGIMEN_RIESGO,
    evaluar_regimen,
)
from investing_bot.senales.velocidad_reddit import VelocidadReddit


def _repo(sesion: AsyncSession, fecha: date = FECHA) -> RepositorioPIT:
    return RepositorioPIT(sesion, fecha)


# --- S1: deriva post-noticia ----------------------------------------------


async def test_deriva_sin_noticias_no_aporta(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion)
    await sesion.commit()

    componente = await DerivaNoticias(0.40).calcular(_repo(sesion), "NVDA", FECHA)

    assert not componente.datos_suficientes
    assert componente.puntos == 0.0


async def test_deriva_positiva_confirmada_por_el_precio(sesion: AsyncSession) -> None:
    """Titular bueno + precio subiendo = deriva. Es el caso que la senal busca."""
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, variacion_diaria=0.01)  # sube
    await sembrar_noticia(sesion, sentimiento=0.8, dias_atras=4)
    await sesion.commit()

    componente = await DerivaNoticias(0.40).calcular(_repo(sesion), "NVDA", FECHA)

    assert componente.datos_suficientes
    assert componente.valor > 0
    assert componente.detalle["confirmado_por_precio"] is True
    assert componente.puntos > 20


async def test_deriva_se_recorta_si_el_precio_no_confirma(sesion: AsyncSession) -> None:
    """Titular bueno con el precio cayendo es ruido con buen vocabulario."""
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, variacion_diaria=-0.01)  # baja
    await sembrar_noticia(sesion, sentimiento=0.8, dias_atras=4)
    await sesion.commit()

    componente = await DerivaNoticias(0.40).calcular(_repo(sesion), "NVDA", FECHA)

    assert componente.detalle["confirmado_por_precio"] is False
    assert 0 < componente.valor < 0.5


async def test_deriva_ignora_las_noticias_de_las_ultimas_horas(
    sesion: AsyncSession,
) -> None:
    """No se compite por velocidad: las noticias de hoy no cuentan todavia."""
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, variacion_diaria=0.01)
    await sembrar_noticia(sesion, dias_atras=0)
    await sesion.commit()

    componente = await DerivaNoticias(0.40).calcular(_repo(sesion), "NVDA", FECHA)
    assert not componente.datos_suficientes


async def test_deriva_ignora_las_noticias_de_baja_confianza(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, variacion_diaria=0.01)
    await sembrar_noticia(sesion, sentimiento=0.9, confianza=0.1, dias_atras=4)
    await sesion.commit()

    componente = await DerivaNoticias(0.40).calcular(_repo(sesion), "NVDA", FECHA)
    assert not componente.datos_suficientes


async def test_deriva_negativa_da_puntos_negativos(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_precios(sesion, variacion_diaria=-0.01)
    await sembrar_noticia(sesion, sentimiento=-0.8, dias_atras=4)
    await sesion.commit()

    componente = await DerivaNoticias(0.40).calcular(_repo(sesion), "NVDA", FECHA)
    assert componente.valor < 0
    assert componente.puntos < 0


# --- S2: velocidad de Reddit ----------------------------------------------


async def test_reddit_sin_historial_no_aporta(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_reddit(sesion, dias=3)
    await sesion.commit()

    componente = await VelocidadReddit(0.25).calcular(_repo(sesion), "NVDA", FECHA)

    assert not componente.datos_suficientes
    assert "historial" in componente.resumen


async def test_reddit_detecta_el_pico_de_menciones(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_reddit(sesion, dias=25, menciones_base=10, menciones_hoy=90, sentimiento=0.7)
    await sesion.commit()

    componente = await VelocidadReddit(0.25).calcular(_repo(sesion), "NVDA", FECHA)

    assert componente.datos_suficientes
    assert componente.detalle["z_score"] > 3
    assert componente.valor > 0.5


async def test_reddit_con_sentimiento_negativo_da_senal_negativa(
    sesion: AsyncSession,
) -> None:
    """Un pico de menciones negativas es senal de venta, no de compra."""
    await sembrar_ticker(sesion)
    await sembrar_reddit(sesion, dias=25, menciones_base=10, menciones_hoy=90, sentimiento=-0.7)
    await sesion.commit()

    componente = await VelocidadReddit(0.25).calcular(_repo(sesion), "NVDA", FECHA)
    assert componente.valor < 0


async def test_reddit_con_sentimiento_neutro_no_aporta(sesion: AsyncSession) -> None:
    """Mucho ruido sin direccion no es informacion."""
    await sembrar_ticker(sesion)
    await sembrar_reddit(sesion, dias=25, menciones_base=10, menciones_hoy=90, sentimiento=0.0)
    await sesion.commit()

    componente = await VelocidadReddit(0.25).calcular(_repo(sesion), "NVDA", FECHA)
    assert not componente.datos_suficientes
    assert "neutro" in componente.resumen


# --- S3: consenso del Congreso --------------------------------------------


async def test_congreso_con_un_solo_miembro_no_es_consenso(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_congreso(sesion, miembros=1)
    await sesion.commit()

    componente = await ConsensoCongreso(0.15).calcular(_repo(sesion), "NVDA", FECHA)

    assert not componente.datos_suficientes
    assert "consenso" in componente.resumen


async def test_congreso_con_varios_compradores_aporta_positivo(
    sesion: AsyncSession,
) -> None:
    await sembrar_ticker(sesion)
    await sembrar_congreso(sesion, miembros=4, tipo="compra")
    await sesion.commit()

    componente = await ConsensoCongreso(0.15).calcular(_repo(sesion), "NVDA", FECHA)

    assert componente.datos_suficientes
    assert componente.valor > 0
    assert componente.detalle["miembros_distintos"] == 4


async def test_congreso_con_ventas_aporta_negativo(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion)
    await sembrar_congreso(sesion, miembros=4, tipo="venta")
    await sesion.commit()

    componente = await ConsensoCongreso(0.15).calcular(_repo(sesion), "NVDA", FECHA)
    assert componente.valor < 0


async def test_congreso_penaliza_el_disclosure_antiguo(sesion: AsyncSession) -> None:
    """Lo que se supo hace tres meses ya lo descontó el mercado."""
    await sembrar_ticker(sesion, symbol="NVDA")
    await sembrar_ticker(sesion, symbol="AMD")
    await sembrar_congreso(sesion, symbol="NVDA", miembros=3, dias_atras=10, dias_retraso=5)
    await sembrar_congreso(sesion, symbol="AMD", miembros=3, dias_atras=85, dias_retraso=1)
    await sesion.commit()

    reciente = await ConsensoCongreso(0.15).calcular(_repo(sesion), "NVDA", FECHA)
    antiguo = await ConsensoCongreso(0.15).calcular(_repo(sesion), "AMD", FECHA)

    assert reciente.valor > antiguo.valor


# --- S4: regimen de mercado -----------------------------------------------


async def test_regimen_alcista_con_el_indice_sobre_su_media(sesion: AsyncSession) -> None:
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, symbol="SPY", dias=260, variacion_diaria=0.002)
    await sesion.commit()

    estado = await evaluar_regimen(_repo(sesion), FECHA, symbol="SPY", dias_media=200)

    assert estado.regimen == REGIMEN_ALCISTA
    assert not estado.modo_defensivo


async def test_regimen_de_riesgo_con_el_indice_bajo_su_media(sesion: AsyncSession) -> None:
    """Probado con una serie bajista: es lo que activa el modo defensivo."""
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, symbol="SPY", dias=260, variacion_diaria=-0.002)
    await sesion.commit()

    estado = await evaluar_regimen(_repo(sesion), FECHA, symbol="SPY", dias_media=200)

    assert estado.regimen == REGIMEN_RIESGO
    assert estado.modo_defensivo
    assert estado.detalle["distancia_pct"] < 0


async def test_sin_historial_suficiente_el_regimen_es_defensivo(
    sesion: AsyncSession,
) -> None:
    """No saber en que mercado estamos no es razon para comprar."""
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, symbol="SPY", dias=30)
    await sesion.commit()

    estado = await evaluar_regimen(_repo(sesion), FECHA, symbol="SPY", dias_media=200)

    assert estado.regimen == REGIMEN_DESCONOCIDO
    assert estado.modo_defensivo is True


async def test_el_regimen_tambien_respeta_el_point_in_time(sesion: AsyncSession) -> None:
    """Evaluado hace un mes, no puede usar los precios del ultimo mes."""
    await sembrar_ticker(sesion, symbol="SPY", sector="ETF")
    await sembrar_precios(sesion, symbol="SPY", dias=260, variacion_diaria=0.002)
    await sesion.commit()

    pasado = FECHA - timedelta(days=200)
    estado = await evaluar_regimen(_repo(sesion, pasado), pasado, symbol="SPY", dias_media=200)

    assert estado.regimen == REGIMEN_DESCONOCIDO  # no habia 200 barras aun
