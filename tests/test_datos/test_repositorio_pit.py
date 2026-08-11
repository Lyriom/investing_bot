"""Los tests mas importantes del repositorio.

El invariante I1 dice que el sistema solo puede leer filas con
`observed_at <= fecha_simulada`. Si esto no se cumple, todo lo demas —senales,
scores, backtests— es ficcion optimista. Estos tests existen para que una
regresion ahi falle ruidosamente en vez de producir resultados brillantes y
falsos.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.datos.repositorio_pit import (
    FugaDeInformacion,
    RepositorioPIT,
    fin_del_dia,
    verificar_pit,
)
from investing_bot.modelos import CongresoTrade, Noticia, PrecioDiario, RedditDiario, Ticker

FECHA = date(2026, 8, 11)
AYER = datetime(2026, 8, 10, 21, 0, tzinfo=UTC)
MANANA = datetime(2026, 8, 12, 21, 0, tzinfo=UTC)


async def _ticker(sesion: AsyncSession) -> None:
    sesion.add(Ticker(symbol="NVDA", nombre="NVIDIA Corporation", en_whitelist=True))
    await sesion.flush()


# --- Precios ---------------------------------------------------------------


async def test_no_se_ve_una_barra_observada_en_el_futuro(sesion: AsyncSession) -> None:
    """El caso central: un precio que el sistema aun no podia conocer."""
    await _ticker(sesion)
    sesion.add(
        PrecioDiario(
            symbol="NVDA", fecha=date(2026, 8, 10), cierre=Decimal("100"), observed_at=AYER
        )
    )
    sesion.add(
        PrecioDiario(
            symbol="NVDA", fecha=date(2026, 8, 12), cierre=Decimal("999"), observed_at=MANANA
        )
    )
    await sesion.commit()

    barras = await RepositorioPIT(sesion, FECHA).precios("NVDA")

    assert [b.fecha for b in barras] == [date(2026, 8, 10)]
    assert all(float(b.cierre) != 999 for b in barras)


async def test_el_ultimo_cierre_es_el_ultimo_conocido_no_el_ultimo_existente(
    sesion: AsyncSession,
) -> None:
    """El precio de referencia de una sugerencia no puede venir del futuro."""
    await _ticker(sesion)
    sesion.add(
        PrecioDiario(
            symbol="NVDA", fecha=date(2026, 8, 10), cierre=Decimal("100"), observed_at=AYER
        )
    )
    sesion.add(
        PrecioDiario(
            symbol="NVDA", fecha=date(2026, 8, 12), cierre=Decimal("999"), observed_at=MANANA
        )
    )
    await sesion.commit()

    barra = await RepositorioPIT(sesion, FECHA).ultimo_cierre("NVDA")

    assert barra is not None
    assert float(barra.cierre) == 100.0


# --- Las otras tres fuentes ------------------------------------------------


async def test_no_se_ve_una_noticia_observada_en_el_futuro(sesion: AsyncSession) -> None:
    await _ticker(sesion)
    sesion.add(
        Noticia(
            symbol="NVDA",
            titulo="Conocida",
            hash_contenido="a" * 64,
            event_at=AYER,
            observed_at=AYER,
        )
    )
    sesion.add(
        Noticia(
            symbol="NVDA",
            titulo="Del futuro",
            hash_contenido="b" * 64,
            event_at=AYER,
            observed_at=MANANA,
        )
    )
    await sesion.commit()

    noticias = await RepositorioPIT(sesion, FECHA).noticias("NVDA")
    assert [n.titulo for n in noticias] == ["Conocida"]


async def test_no_se_ven_menciones_de_reddit_del_futuro(sesion: AsyncSession) -> None:
    await _ticker(sesion)
    sesion.add(
        RedditDiario(
            symbol="NVDA",
            fecha=date(2026, 8, 10),
            subreddit="stocks",
            menciones=5,
            observed_at=AYER,
        )
    )
    sesion.add(
        RedditDiario(
            symbol="NVDA",
            fecha=date(2026, 8, 10),
            subreddit="investing",
            menciones=99,
            observed_at=MANANA,
        )
    )
    await sesion.commit()

    filas = await RepositorioPIT(sesion, FECHA).reddit("NVDA")
    assert [f.menciones for f in filas] == [5]


async def test_el_trade_del_congreso_se_filtra_por_disclosure_no_por_transaccion(
    sesion: AsyncSession,
) -> None:
    """El bug mas caro del dominio, en un test.

    Un trade ejecutado el 1 de marzo y divulgado el 14 de abril NO puede
    aparecer en un backtest del 15 de marzo, por mucho que su
    `fecha_transaccion` sea anterior. Usar `event_at` en vez de `observed_at`
    produce retornos espectaculares y completamente falsos.
    """
    await _ticker(sesion)
    sesion.add(
        CongresoTrade(
            miembro="Un legislador",
            symbol="NVDA",
            tipo="compra",
            monto_min=Decimal("15000"),
            fecha_transaccion=date(2026, 3, 1),
            fecha_disclosure=date(2026, 4, 14),
            observed_at=datetime(2026, 4, 14, 12, 0, tzinfo=UTC),
        )
    )
    await sesion.commit()

    # El 15 de marzo el trade ya ocurrio, pero todavia no era publico.
    en_marzo = await RepositorioPIT(sesion, date(2026, 3, 15)).congreso("NVDA", dias=365)
    assert en_marzo == []

    # El 20 de abril ya se sabia.
    en_abril = await RepositorioPIT(sesion, date(2026, 4, 20)).congreso("NVDA", dias=365)
    assert len(en_abril) == 1


# --- La red de seguridad ---------------------------------------------------


def test_verificar_pit_falla_ruidosamente_con_una_fila_futura() -> None:
    """Cualquier consulta escrita fuera del repositorio debe pasar por aqui."""

    class FilaFalsa:
        observed_at = MANANA

    with pytest.raises(FugaDeInformacion, match="no podia conocer"):
        verificar_pit([FilaFalsa()], fin_del_dia(FECHA), "prueba")


def test_verificar_pit_deja_pasar_lo_que_ya_se_sabia() -> None:
    class FilaFalsa:
        observed_at = AYER

    assert len(verificar_pit([FilaFalsa()], fin_del_dia(FECHA))) == 1


def test_verificar_pit_acepta_datetimes_sin_zona() -> None:
    """sqlite devuelve datetimes naive; se tratan como UTC, no se rechazan."""

    class FilaFalsa:
        observed_at = datetime(2026, 8, 10, 21, 0)  # noqa: DTZ001 - a proposito

    assert len(verificar_pit([FilaFalsa()], fin_del_dia(FECHA))) == 1


def test_el_corte_de_una_fecha_es_el_final_del_dia() -> None:
    """Al cerrar la sesion del dia D, ya se conoce todo lo observado durante D."""
    corte = fin_del_dia(FECHA)
    assert corte.date() == FECHA
    assert corte.hour == 23
    assert corte > datetime(2026, 8, 11, 23, 0, tzinfo=UTC)


async def test_el_repositorio_acepta_fecha_o_datetime(sesion: AsyncSession) -> None:
    """Pasar un `datetime` acota mas fino que pasar una `date`."""
    await _ticker(sesion)
    media_manana = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)
    sesion.add(
        Noticia(
            symbol="NVDA",
            titulo="De la tarde",
            hash_contenido="c" * 64,
            event_at=media_manana,
            observed_at=datetime(2026, 8, 11, 20, 0, tzinfo=UTC),
        )
    )
    await sesion.commit()

    del_dia = await RepositorioPIT(sesion, FECHA).noticias("NVDA")
    de_la_manana = await RepositorioPIT(sesion, media_manana).noticias("NVDA")

    assert len(del_dia) == 1
    assert de_la_manana == []


async def test_la_ventana_de_dias_no_sustituye_al_filtro_pit(sesion: AsyncSession) -> None:
    """Aunque se pida una ventana amplia, lo del futuro sigue sin verse."""
    await _ticker(sesion)
    for dias_atras in range(1, 5):
        fecha = FECHA - timedelta(days=dias_atras)
        sesion.add(
            PrecioDiario(
                symbol="NVDA",
                fecha=fecha,
                cierre=Decimal("100"),
                observed_at=datetime.combine(fecha, datetime.min.time(), tzinfo=UTC),
            )
        )
    sesion.add(
        PrecioDiario(
            symbol="NVDA", fecha=date(2026, 8, 20), cierre=Decimal("999"), observed_at=MANANA
        )
    )
    await sesion.commit()

    barras = await RepositorioPIT(sesion, FECHA).precios("NVDA", dias=365)
    assert len(barras) == 4
    assert all(b.observed_at.replace(tzinfo=UTC) <= fin_del_dia(FECHA) for b in barras)
