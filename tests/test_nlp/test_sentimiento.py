"""Tests del clasificador de sentimiento.

Se prueba el respaldo lexico, que es el que corre cuando torch no esta
instalado. FinBERT no se descarga en tests: son 400 MB y una dependencia de
red que el SPEC prohibe en la suite.
"""

from __future__ import annotations

from investing_bot.nlp.sentimiento import (
    ETIQUETA_LEXICO,
    ClasificadorSentimiento,
)

CLASIFICADOR = ClasificadorSentimiento(forzar_lexico=True)


def test_un_titular_positivo_da_sentimiento_positivo() -> None:
    resultado = CLASIFICADOR.clasificar("Nvidia beats estimates and surges to record profit")
    assert resultado.valor > 0
    assert resultado.confianza > 0


def test_un_titular_negativo_da_sentimiento_negativo() -> None:
    resultado = CLASIFICADOR.clasificar("Tesla plunges after lawsuit and recall warning")
    assert resultado.valor < 0


def test_un_titular_sin_carga_es_neutro_y_sin_confianza() -> None:
    """Neutro con confianza 0: la senal S1 lo descartara, que es lo correcto."""
    resultado = CLASIFICADOR.clasificar("The company will hold a meeting on Tuesday")
    assert resultado.valor == 0.0
    assert resultado.confianza == 0.0


def test_el_modelo_usado_queda_declarado() -> None:
    """Nunca se puede confundir una salida del lexico con una de FinBERT."""
    assert CLASIFICADOR.clasificar("beats estimates").modelo == ETIQUETA_LEXICO
    assert CLASIFICADOR.modelo_activo == ETIQUETA_LEXICO


def test_la_confianza_del_lexico_esta_topada() -> None:
    """Un lexico de 60 palabras no merece la confianza de un modelo entrenado."""
    resultado = CLASIFICADOR.clasificar(
        "beats surges soars rally jumps gains record upgrade strong growth profit"
    )
    assert resultado.confianza <= 0.6


def test_clasificar_lote_devuelve_uno_por_texto() -> None:
    textos = ["beats estimates", "plunges after recall", "meeting on Tuesday"]
    resultados = CLASIFICADOR.clasificar_lote(textos)

    assert len(resultados) == 3
    assert resultados[0].valor > 0
    assert resultados[1].valor < 0
    assert resultados[2].valor == 0


def test_lote_vacio_no_revienta() -> None:
    assert CLASIFICADOR.clasificar_lote([]) == []


def test_el_signo_no_depende_del_orden_de_las_palabras() -> None:
    a = CLASIFICADOR.clasificar("record profit despite a small loss")
    b = CLASIFICADOR.clasificar("a small loss despite record profit")
    assert a.valor == b.valor
