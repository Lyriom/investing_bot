"""Deduplicacion: diez portales replicando un cable son un dato, no diez."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos import Noticia
from investing_bot.normalizador.deduplicador import (
    agrupar_lote,
    buscar_original,
    hash_titular,
    similitud_jaccard,
    son_casi_iguales,
    tokens_significativos,
)

AHORA = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def test_las_palabras_vacias_no_cuentan() -> None:
    assert tokens_significativos("Apple beats the estimates") == ["apple", "beats", "estimates"]


def test_el_hash_ignora_puntuacion_y_palabras_vacias() -> None:
    assert hash_titular("Apple beats estimates") == hash_titular("Apple beats the estimates!")


def test_el_hash_distingue_titulares_distintos() -> None:
    assert hash_titular("Apple beats estimates") != hash_titular("Apple misses estimates")


def test_el_hash_conserva_el_orden_de_las_palabras() -> None:
    """ "Nvidia supera a AMD" y "AMD supera a Nvidia" son noticias opuestas."""
    assert hash_titular("Nvidia beats AMD") != hash_titular("AMD beats Nvidia")


def test_jaccard_entre_titulares_identicos_es_uno() -> None:
    tokens = tokens_significativos("Apple beats estimates")
    assert similitud_jaccard(tokens, tokens) == 1.0


def test_detecta_el_titular_reescrito() -> None:
    assert son_casi_iguales(
        "Nvidia reports record quarterly revenue",
        "Nvidia reports record quarterly revenue!",
    )


def test_no_confunde_noticias_distintas() -> None:
    assert not son_casi_iguales(
        "Nvidia reports record quarterly revenue",
        "Tesla recalls 200000 vehicles over brake issue",
    )


# --- Agrupacion en lote ----------------------------------------------------


def test_agrupar_marca_replicas_y_deja_un_original() -> None:
    titulares = [
        (1, "Nvidia reports record revenue", AHORA),
        (2, "Nvidia reports record revenue", AHORA + timedelta(hours=1)),
        (3, "Nvidia reports record revenue", AHORA + timedelta(hours=2)),
        (4, "Tesla recalls vehicles over brakes", AHORA + timedelta(hours=3)),
    ]
    grupos = agrupar_lote(titulares)

    assert len(grupos) == 2
    principal = next(g for g in grupos if g.original == 1)
    assert sorted(principal.replicas) == [2, 3]


def test_gana_el_mas_antiguo_como_original() -> None:
    """El primero en publicar es el original; el resto son replicas."""
    titulares = [
        (99, "Apple beats estimates", AHORA + timedelta(hours=5)),
        (1, "Apple beats estimates", AHORA),
    ]
    grupos = agrupar_lote(titulares)
    assert len(grupos) == 1
    assert grupos[0].original == 1
    assert grupos[0].replicas == [99]


def test_fuera_de_la_ventana_ya_no_es_duplicado() -> None:
    """El mismo titular seis meses despues es otra noticia."""
    titulares = [
        (1, "Apple beats estimates", AHORA),
        (2, "Apple beats estimates", AHORA + timedelta(days=90)),
    ]
    assert len(agrupar_lote(titulares)) == 2


def test_los_digitos_no_se_descartan_como_ruido() -> None:
    """En un titular financiero la cifra suele ser la noticia."""
    assert "2" in tokens_significativos("Tesla recalls 2 million vehicles")
    assert not son_casi_iguales(
        "Tesla recalls 2 million vehicles", "Tesla recalls 3 million vehicles"
    )


def test_la_deduplicacion_reduce_el_volumen_de_forma_medible() -> None:
    """Criterio de la FASE 1: reportar el porcentaje de reduccion."""
    cable = "Nvidia reports record quarterly revenue"
    propias = [
        "Tesla recalls vehicles over a brake defect",
        "Pfizer wins approval for its new vaccine",
    ]
    titulares = [
        (i, cable if i < 8 else propias[i - 8], AHORA + timedelta(minutes=i)) for i in range(10)
    ]
    grupos = agrupar_lote(titulares)
    reduccion = 1 - len(grupos) / len(titulares)

    assert len(grupos) == 3  # 1 cable + 2 noticias propias
    assert reduccion == 0.7


# --- Contra la base --------------------------------------------------------


async def test_buscar_original_encuentra_la_replica_exacta(sesion: AsyncSession) -> None:
    sesion.add(
        Noticia(
            titulo="Nvidia reports record revenue",
            hash_contenido=hash_titular("Nvidia reports record revenue"),
            event_at=AHORA,
            observed_at=AHORA,
        )
    )
    await sesion.commit()

    original = await buscar_original(sesion, "Nvidia reports record revenue!", AHORA)
    assert original is not None


async def test_buscar_original_no_devuelve_nada_si_es_noticia_nueva(
    sesion: AsyncSession,
) -> None:
    sesion.add(
        Noticia(
            titulo="Nvidia reports record revenue",
            hash_contenido=hash_titular("Nvidia reports record revenue"),
            event_at=AHORA,
            observed_at=AHORA,
        )
    )
    await sesion.commit()

    assert await buscar_original(sesion, "Tesla recalls 200000 vehicles", AHORA) is None


async def test_buscar_original_respeta_la_ventana(sesion: AsyncSession) -> None:
    sesion.add(
        Noticia(
            titulo="Apple beats estimates",
            hash_contenido=hash_titular("Apple beats estimates"),
            event_at=AHORA,
            observed_at=AHORA,
        )
    )
    await sesion.commit()

    lejano = AHORA + timedelta(days=30)
    assert await buscar_original(sesion, "Apple beats estimates", lejano) is None
