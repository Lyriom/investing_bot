"""Tests del ingestor de Reddit. Sin red y sin credenciales."""

from __future__ import annotations

from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.ingestores.reddit import IngestorReddit, Publicacion, agregar_menciones
from investing_bot.modelos import RedditDiario, Ticker
from investing_bot.nlp.sentimiento import ClasificadorSentimiento
from investing_bot.normalizador.entidades import ResolutorEntidades

OBSERVADO = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
DIA = datetime(2026, 8, 11, 12, 0, tzinfo=UTC).timestamp()

RESOLUTOR = ResolutorEntidades({"NVDA": "NVIDIA Corporation", "AMD": "Advanced Micro Devices"})
CLASIFICADOR = ClasificadorSentimiento(forzar_lexico=True)


def _post(titulo: str, texto: str = "", upvotes: int = 100) -> Publicacion:
    return Publicacion(
        subreddit="wallstreetbets",
        titulo=titulo,
        texto=texto,
        upvotes=upvotes,
        creado_utc=DIA,
    )


def test_se_agregan_las_menciones_por_ticker_dia_y_subreddit() -> None:
    filas = agregar_menciones(
        [_post("$NVDA to the moon"), _post("buying more $NVDA")],
        RESOLUTOR,
        CLASIFICADOR,
        OBSERVADO,
    )

    assert len(filas) == 1
    assert filas[0]["symbol"] == "NVDA"
    assert filas[0]["menciones"] == 2
    assert filas[0]["fecha"] == date(2026, 8, 11)


def test_un_post_que_nombra_dos_tickers_cuenta_para_los_dos() -> None:
    filas = agregar_menciones([_post("$NVDA vs $AMD")], RESOLUTOR, CLASIFICADOR, OBSERVADO)
    assert {f["symbol"] for f in filas} == {"NVDA", "AMD"}
    assert all(f["menciones"] == 1 for f in filas)


def test_repetir_el_ticker_en_un_post_cuenta_una_sola_vez() -> None:
    """Si no, un solo autor entusiasta moveria la senal el solo."""
    filas = agregar_menciones(
        [_post("$NVDA $NVDA $NVDA nvidia nvidia")], RESOLUTOR, CLASIFICADOR, OBSERVADO
    )
    assert len(filas) == 1
    assert filas[0]["menciones"] == 1


def test_los_upvotes_se_acumulan() -> None:
    filas = agregar_menciones(
        [_post("$NVDA", upvotes=100), _post("$NVDA otra vez", upvotes=250)],
        RESOLUTOR,
        CLASIFICADOR,
        OBSERVADO,
    )
    assert filas[0]["upvotes_totales"] == 350


def test_un_post_sin_tickers_no_produce_filas() -> None:
    assert (
        agregar_menciones([_post("me gustan las acciones")], RESOLUTOR, CLASIFICADOR, OBSERVADO)
        == []
    )


def test_se_calcula_el_sentimiento_promedio() -> None:
    filas = agregar_menciones(
        [_post("$NVDA beats estimates and surges"), _post("$NVDA record profit growth")],
        RESOLUTOR,
        CLASIFICADOR,
        OBSERVADO,
    )
    assert filas[0]["sentimiento_promedio"] is not None
    assert float(filas[0]["sentimiento_promedio"]) > 0


async def test_sin_credenciales_falla_con_un_mensaje_util(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    resultado = await IngestorReddit(fabrica_sesiones=fabrica).ejecutar()

    assert not resultado.exito
    assert "REDDIT_CLIENT_ID" in resultado.errores[0]
    assert "reddit.com/prefs/apps" in resultado.errores[0]


async def test_ingesta_de_reddit_es_idempotente(
    sesion: AsyncSession, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    sesion.add(Ticker(symbol="NVDA", nombre="NVIDIA Corporation", en_whitelist=True))
    await sesion.commit()

    def descargador(subreddits: object, limite: object, desde: object) -> list[Publicacion]:
        return [_post("$NVDA looking strong")]

    ingestor = IngestorReddit(
        fabrica_sesiones=fabrica, descargador=descargador, clasificador=CLASIFICADOR
    )
    primero = await ingestor.ejecutar()
    segundo = await ingestor.ejecutar()

    assert primero.filas_nuevas == 1
    assert segundo.filas_nuevas == 0
    total = await sesion.scalar(sa.select(sa.func.count()).select_from(RedditDiario))
    assert total == 1
