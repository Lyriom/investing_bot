"""Tests del gestor de riesgo: lo que importa es que sepa decir que no."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from investing_bot.config import Configuracion
from investing_bot.modelos import Ticker
from investing_bot.riesgo.gestor import EstadoCartera, GestorRiesgo
from investing_bot.senales.base import ComponenteSenal
from investing_bot.senales.motor import ResultadoScore
from investing_bot.senales.regimen import REGIMEN_ALCISTA, REGIMEN_RIESGO, EstadoRegimen

FECHA = date(2026, 8, 11)


def _config(**extra: object) -> Configuracion:
    base: dict[str, object] = {
        "_env_file": None,
        "capital_total_usd": Decimal("500"),
        "max_posiciones_abiertas": 5,
        "max_pct_por_posicion": Decimal("0.25"),
        "min_tamano_posicion_usd": Decimal("50"),
        "max_sugerencias_por_dia": 2,
        "umbral_sugerencia": 60,
    }
    base.update(extra)
    return Configuracion(**base)  # type: ignore[arg-type]


def _regimen(defensivo: bool = False) -> EstadoRegimen:
    return EstadoRegimen(
        regimen=REGIMEN_RIESGO if defensivo else REGIMEN_ALCISTA,
        modo_defensivo=defensivo,
        detalle={},
        resumen="SPY bajo su MA200" if defensivo else "SPY sobre su MA200",
    )


def _score(
    symbol: str = "NVDA",
    score: float = 75.0,
    precio: float | None = 100.0,
    defensivo: bool = False,
    con_datos: bool = True,
) -> ResultadoScore:
    return ResultadoScore(
        symbol=symbol,
        fecha=FECHA,
        score=score,
        componentes=[
            ComponenteSenal(
                nombre="deriva",
                valor=0.6,
                peso=0.40,
                datos_suficientes=con_datos,
                resumen="sorpresa positiva",
            )
        ],
        regimen=_regimen(defensivo),
        version_modelo="pesos-v1",
        score_antes_regimen=score,
        precio_referencia=precio,
    )


def _ticker(
    symbol: str = "NVDA", sector: str = "Semiconductores", volumen: int = 50_000_000
) -> Ticker:
    return Ticker(
        symbol=symbol,
        sector=sector,
        en_whitelist=True,
        activo=True,
        volumen_promedio_30d=Decimal(volumen),
    )


# --- El caso que si pasa ---------------------------------------------------


def test_un_score_alto_con_todo_en_orden_produce_una_propuesta() -> None:
    decision = GestorRiesgo(_config()).evaluar([_score()], EstadoCartera(), {"NVDA": _ticker()})

    assert len(decision.propuestas) == 1
    propuesta = decision.propuestas[0]
    assert propuesta.symbol == "NVDA"
    assert propuesta.stop < propuesta.precio_referencia
    assert propuesta.tamano_usd == 125.0  # 25 % de 500
    assert propuesta.distancia_stop_pct == pytest.approx(-0.08, abs=1e-9)


def test_toda_propuesta_trae_desglose_stop_y_tamano() -> None:
    """Invariante I3: sin las tres cosas no se emite."""
    decision = GestorRiesgo(_config()).evaluar([_score()], EstadoCartera(), {"NVDA": _ticker()})
    propuesta = decision.propuestas[0]

    assert propuesta.razon
    assert propuesta.stop > 0
    assert propuesta.tamano_usd > 0
    assert propuesta.resultado.version_modelo


# --- Los vetos -------------------------------------------------------------


def test_por_debajo_del_umbral_no_se_sugiere_nada() -> None:
    decision = GestorRiesgo(_config()).evaluar(
        [_score(score=55)], EstadoCartera(), {"NVDA": _ticker()}
    )
    assert decision.propuestas == []
    assert decision.vetos == []  # no destacar no es un veto


def test_el_modo_defensivo_veta_todas_las_compras() -> None:
    decision = GestorRiesgo(_config()).evaluar(
        [_score(defensivo=True)], EstadoCartera(), {"NVDA": _ticker()}
    )

    assert decision.propuestas == []
    assert decision.modo_defensivo
    assert decision.vetos[0].regla == "modo_defensivo"


def test_la_perdida_mensual_excesiva_activa_el_modo_defensivo() -> None:
    estado = EstadoCartera(perdida_mensual_pct=-0.15)
    decision = GestorRiesgo(_config(max_perdida_mensual_pct=Decimal("0.10"))).evaluar(
        [_score()], estado, {"NVDA": _ticker()}
    )

    assert decision.modo_defensivo
    assert "perdida mensual" in decision.motivo_defensivo


def test_no_se_supera_el_maximo_de_posiciones_abiertas() -> None:
    estado = EstadoCartera(posiciones_abiertas={f"T{i}": "Otro" for i in range(5)})
    decision = GestorRiesgo(_config(max_posiciones_abiertas=5)).evaluar(
        [_score()], estado, {"NVDA": _ticker()}
    )
    assert decision.vetos[0].regla == "max_posiciones"


def test_no_se_duplica_una_posicion_ya_abierta() -> None:
    estado = EstadoCartera(posiciones_abiertas={"NVDA": "Semiconductores"})
    decision = GestorRiesgo(_config()).evaluar([_score()], estado, {"NVDA": _ticker()})
    assert decision.vetos[0].regla == "posicion_abierta"


def test_se_respeta_el_cooldown_del_mismo_ticker() -> None:
    estado = EstadoCartera(ultimo_sugerido={"NVDA": FECHA - timedelta(days=2)})
    decision = GestorRiesgo(_config(dias_cooldown_mismo_ticker=5)).evaluar(
        [_score()], estado, {"NVDA": _ticker()}
    )
    assert decision.vetos[0].regla == "cooldown"


def test_se_limita_la_concentracion_por_sector() -> None:
    estado = EstadoCartera(
        posiciones_abiertas={"AMD": "Semiconductores", "INTC": "Semiconductores"}
    )
    decision = GestorRiesgo(_config(max_posiciones_por_sector=2)).evaluar(
        [_score()], estado, {"NVDA": _ticker()}
    )
    assert decision.vetos[0].regla == "concentracion_sector"


def test_se_veta_el_ticker_ilíquido() -> None:
    decision = GestorRiesgo(_config(min_volumen_diario=1_000_000)).evaluar(
        [_score()], EstadoCartera(), {"NVDA": _ticker(volumen=100_000)}
    )
    assert decision.vetos[0].regla == "liquidez"


def test_se_veta_el_precio_por_debajo_del_minimo() -> None:
    decision = GestorRiesgo(_config(min_precio_accion=Decimal("5"))).evaluar(
        [_score(precio=2.0)], EstadoCartera(), {"NVDA": _ticker()}
    )
    assert decision.vetos[0].regla == "precio_minimo"


def test_se_veta_lo_que_no_esta_en_la_whitelist() -> None:
    decision = GestorRiesgo(_config()).evaluar([_score()], EstadoCartera(), {})
    assert decision.vetos[0].regla == "whitelist"


def test_se_veta_un_score_sin_ninguna_senal_con_datos() -> None:
    """Un score de 75 construido sobre cero evidencia no significa nada."""
    decision = GestorRiesgo(_config()).evaluar(
        [_score(con_datos=False)], EstadoCartera(), {"NVDA": _ticker()}
    )
    assert decision.vetos[0].regla == "sin_evidencia"


def test_no_se_superan_las_sugerencias_diarias() -> None:
    scores = [_score(symbol=s, score=80) for s in ("NVDA", "AMD", "AAPL")]
    tickers = {
        "NVDA": _ticker("NVDA", "Semiconductores"),
        "AMD": _ticker("AMD", "Tecnologia"),
        "AAPL": _ticker("AAPL", "Consumo"),
    }
    decision = GestorRiesgo(_config(max_sugerencias_por_dia=2)).evaluar(
        scores, EstadoCartera(), tickers
    )

    assert len(decision.propuestas) == 2
    assert any(v.regla == "max_sugerencias_dia" for v in decision.vetos)


# --- Costo de friccion -----------------------------------------------------


def test_se_veta_cuando_la_friccion_se_come_la_posicion() -> None:
    """Con capital pequeno, la comision fija mata estrategias rentables en papel."""
    config = _config(
        capital_total_usd=Decimal("30"),
        max_pct_por_posicion=Decimal("1.0"),
        min_tamano_posicion_usd=Decimal("10"),
        costo_clearing_usd=Decimal("0.15"),
        max_costo_friccion_pct=Decimal("0.01"),
    )
    decision = GestorRiesgo(config).evaluar([_score()], EstadoCartera(), {"NVDA": _ticker()})

    assert decision.propuestas == []
    assert decision.vetos[0].regla == "costo_friccion"


def test_el_minimo_de_posicion_no_rompe_el_limite_de_concentracion() -> None:
    """El minimo es un filtro de viabilidad, no un objetivo.

    Con capital 150 y un maximo del 25 %, la posicion seria de 37.50 USD, por
    debajo del minimo de 50. Subirla a 50 la dejaria en el 33 % del capital y
    rompería `max_pct_por_posicion`. La respuesta correcta es no operar.
    """
    config = _config(
        capital_total_usd=Decimal("150"),
        max_pct_por_posicion=Decimal("0.25"),
        min_tamano_posicion_usd=Decimal("50"),
    )
    decision = GestorRiesgo(config).evaluar([_score()], EstadoCartera(), {"NVDA": _ticker()})

    assert decision.propuestas == []
    assert decision.vetos[0].regla == "tamano_minimo"
    assert "concentracion" in decision.vetos[0].motivo


def test_ninguna_propuesta_supera_el_maximo_por_posicion() -> None:
    config = _config(capital_total_usd=Decimal("500"), max_pct_por_posicion=Decimal("0.25"))
    decision = GestorRiesgo(config).evaluar([_score()], EstadoCartera(), {"NVDA": _ticker()})

    propuesta = decision.propuestas[0]
    assert propuesta.tamano_usd <= 500 * 0.25


def test_con_una_posicion_grande_la_friccion_deja_de_ser_problema() -> None:
    config = _config(
        capital_total_usd=Decimal("2000"),
        max_pct_por_posicion=Decimal("0.25"),
        costo_clearing_usd=Decimal("0.15"),
        max_costo_friccion_pct=Decimal("0.01"),
    )
    decision = GestorRiesgo(config).evaluar([_score()], EstadoCartera(), {"NVDA": _ticker()})

    assert len(decision.propuestas) == 1
    assert decision.propuestas[0].costo_pct < 0.01


def test_el_costo_cuenta_la_ida_y_la_vuelta() -> None:
    """Mirar solo la compra es contar la mitad del viaje."""
    config = _config(
        capital_total_usd=Decimal("1000"),
        max_pct_por_posicion=Decimal("0.5"),
        costo_clearing_usd=Decimal("0.15"),
        spread_estimado_pct=Decimal("0"),
    )
    decision = GestorRiesgo(config).evaluar([_score()], EstadoCartera(), {"NVDA": _ticker()})

    assert decision.propuestas[0].costo_estimado_usd == pytest.approx(0.30)
