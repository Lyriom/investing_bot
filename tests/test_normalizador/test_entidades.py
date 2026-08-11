"""Resolucion de entidades.

Criterio de aceptacion de la FASE 1: **la resolucion no produce falsos
positivos sobre un set curado**. Atribuir una noticia al ticker equivocado
envenena la senal de ese ticker y no se nota nunca; perder una noticia solo
cuesta una observacion.
"""

from __future__ import annotations

import pytest

from investing_bot.normalizador.entidades import (
    ResolutorEntidades,
    normalizar_nombre_empresa,
    normalizar_texto,
)

CATALOGO = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "META": "Meta Platforms Inc.",
    "V": "Visa Inc.",
    "MA": "Mastercard Incorporated",
    "T": "AT&T Inc.",
    "HD": "The Home Depot Inc.",
    "KO": "The Coca-Cola Company",
    "ALL": "Allstate Corporation",
    "TSLA": "Tesla Inc.",
    "GOOGL": "Alphabet Inc. Clase A",
}


@pytest.fixture
def resolutor() -> ResolutorEntidades:
    return ResolutorEntidades(CATALOGO)


# --- Normalizacion ---------------------------------------------------------


def test_normalizar_quita_tildes_y_puntuacion() -> None:
    assert normalizar_texto("¡Subió un 8%, según Reuters!") == "subio un 8 segun reuters"


def test_normalizar_nombre_quita_sufijos_societarios() -> None:
    assert normalizar_nombre_empresa("Apple Inc.") == "apple"
    assert normalizar_nombre_empresa("The Home Depot Inc.") == "home depot"
    assert normalizar_nombre_empresa("NVIDIA Corporation") == "nvidia"


# --- Aciertos --------------------------------------------------------------


def test_el_cashtag_es_la_senal_mas_fuerte(resolutor: ResolutorEntidades) -> None:
    coincidencia = resolutor.resolver_uno("comprando $NVDA a saco")
    assert coincidencia is not None
    assert (coincidencia.symbol, coincidencia.metodo) == ("NVDA", "cashtag")


def test_el_nombre_de_empresa_resuelve(resolutor: ResolutorEntidades) -> None:
    coincidencia = resolutor.resolver_uno("Microsoft beats earnings estimates")
    assert coincidencia is not None
    assert coincidencia.symbol == "MSFT"


def test_los_alias_curados_resuelven(resolutor: ResolutorEntidades) -> None:
    assert resolutor.resolver_uno("Google announces layoffs").symbol == "GOOGL"  # type: ignore[union-attr]
    assert resolutor.resolver_uno("coca cola raises guidance").symbol == "KO"  # type: ignore[union-attr]


def test_el_simbolo_suelto_resuelve_si_no_es_ambiguo(resolutor: ResolutorEntidades) -> None:
    coincidencia = resolutor.resolver_uno("NVDA looking strong today")
    assert coincidencia is not None
    assert (coincidencia.symbol, coincidencia.metodo) == ("NVDA", "simbolo_suelto")


# --- Falsos positivos: lo que NO debe resolver -----------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "I think IT is going to be a big year",  # "IT" no es un ticker aqui
        "The new visa policy affects tech workers",  # "visa" no es V
        "This is all meta commentary honestly",  # "meta" no es META
        "I live in MA and pay too much tax",  # "MA" es Massachusetts
        "Watch the 200 day MA on the chart",  # "MA" es media movil
        "ALL of my positions are red",  # "ALL" es una palabra
        "Bought a new HD monitor yesterday",  # "HD" es alta definicion
        "T minus five days to earnings",  # "T" es una letra
        "The CEO said EPS would beat",  # siglas, no tickers
        "AI and EV stocks are hot",  # jerga, no tickers
    ],
)
def test_no_produce_falsos_positivos(resolutor: ResolutorEntidades, texto: str) -> None:
    assert resolutor.resolver(texto) == [], f"falso positivo en: {texto!r}"


def test_los_simbolos_ambiguos_si_resuelven_con_cashtag(
    resolutor: ResolutorEntidades,
) -> None:
    """Con `$` el autor dijo explicitamente de que habla: ahi si se acepta."""
    coincidencia = resolutor.resolver_uno("long $MA into earnings")
    assert coincidencia is not None
    assert coincidencia.symbol == "MA"


def test_dos_tickers_empatados_no_resuelven_a_ninguno(
    resolutor: ResolutorEntidades,
) -> None:
    """Un titular que menciona dos empresas por igual no es evidencia sobre ninguna."""
    texto = "$NVDA and $AAPL both announce results"
    assert len(resolutor.resolver(texto)) == 2
    assert resolutor.resolver_uno(texto) is None


def test_gana_el_metodo_mas_fiable(resolutor: ResolutorEntidades) -> None:
    """Si el mismo ticker aparece de dos formas, se queda la de mayor confianza."""
    coincidencias = resolutor.resolver("Apple sube: $AAPL en maximos")
    assert len(coincidencias) == 1
    assert coincidencias[0].metodo == "cashtag"


def test_un_ticker_fuera_del_catalogo_se_ignora(resolutor: ResolutorEntidades) -> None:
    assert resolutor.resolver("$GME to the moon") == []


def test_texto_vacio_no_revienta(resolutor: ResolutorEntidades) -> None:
    assert resolutor.resolver("") == []
    assert resolutor.resolver_uno("") is None
