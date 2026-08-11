"""Fabricas de datos sinteticos deterministas para los tests de senales."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos import CongresoTrade, Noticia, PrecioDiario, RedditDiario, Ticker

FECHA = date(2026, 8, 11)


def momento(fecha: date, hora: int = 21) -> datetime:
    return datetime.combine(fecha, datetime.min.time(), tzinfo=UTC).replace(hour=hora)


async def sembrar_ticker(
    sesion: AsyncSession,
    symbol: str = "NVDA",
    sector: str = "Semiconductores",
    volumen: int = 50_000_000,
) -> Ticker:
    ticker = Ticker(
        symbol=symbol,
        nombre=f"{symbol} Inc.",
        sector=sector,
        en_whitelist=True,
        activo=True,
        volumen_promedio_30d=Decimal(volumen),
    )
    sesion.add(ticker)
    await sesion.flush()
    return ticker


async def sembrar_precios(
    sesion: AsyncSession,
    symbol: str = "NVDA",
    dias: int = 30,
    precio_inicial: float = 100.0,
    variacion_diaria: float = 0.0,
    hasta: date = FECHA,
) -> None:
    """Serie de cierres con una tendencia lineal fija. Sin aleatoriedad."""
    for indice in range(dias):
        fecha = hasta - timedelta(days=dias - 1 - indice)
        cierre = precio_inicial * (1 + variacion_diaria) ** indice
        sesion.add(
            PrecioDiario(
                symbol=symbol,
                fecha=fecha,
                apertura=Decimal(f"{cierre:.4f}"),
                maximo=Decimal(f"{cierre * 1.01:.4f}"),
                minimo=Decimal(f"{cierre * 0.99:.4f}"),
                cierre=Decimal(f"{cierre:.4f}"),
                cierre_ajustado=Decimal(f"{cierre:.4f}"),
                volumen=50_000_000,
                observed_at=momento(fecha),
            )
        )
    await sesion.flush()


async def sembrar_noticia(
    sesion: AsyncSession,
    symbol: str = "NVDA",
    dias_atras: int = 4,
    sentimiento: float = 0.8,
    confianza: float = 0.9,
    titulo: str | None = None,
    hasta: date = FECHA,
) -> None:
    fecha = hasta - timedelta(days=dias_atras)
    sesion.add(
        Noticia(
            symbol=symbol,
            titulo=titulo or f"{symbol} noticia de hace {dias_atras} dias",
            hash_contenido=f"{symbol}{dias_atras}{sentimiento}".ljust(64, "0")[:64],
            event_at=momento(fecha, hora=13),
            observed_at=momento(fecha, hora=14),
            sentimiento=Decimal(f"{sentimiento:.4f}"),
            confianza=Decimal(f"{confianza:.4f}"),
            modelo_usado="finbert-v1",
            es_duplicado=False,
        )
    )
    await sesion.flush()


async def sembrar_reddit(
    sesion: AsyncSession,
    symbol: str = "NVDA",
    dias: int = 20,
    menciones_base: int = 10,
    menciones_hoy: int | None = None,
    sentimiento: float = 0.6,
    hasta: date = FECHA,
) -> None:
    """Serie plana de menciones con, opcionalmente, un pico el ultimo dia."""
    for indice in range(dias):
        fecha = hasta - timedelta(days=dias - 1 - indice)
        es_hoy = fecha == hasta
        # Pequena oscilacion determinista para que la desviacion no sea cero.
        menciones = menciones_base + (indice % 3)
        if es_hoy and menciones_hoy is not None:
            menciones = menciones_hoy
        sesion.add(
            RedditDiario(
                symbol=symbol,
                fecha=fecha,
                subreddit="wallstreetbets",
                menciones=menciones,
                sentimiento_promedio=Decimal(f"{sentimiento:.4f}"),
                upvotes_totales=menciones * 10,
                observed_at=momento(fecha),
            )
        )
    await sesion.flush()


async def sembrar_congreso(
    sesion: AsyncSession,
    symbol: str = "NVDA",
    miembros: int = 3,
    tipo: str = "compra",
    dias_atras: int = 60,
    dias_retraso: int = 40,
    hasta: date = FECHA,
) -> None:
    """Trades del Congreso ya divulgados.

    `dias_atras` tiene que ser mayor que `dias_retraso`: un trade de hace 30
    dias con 40 de retraso de disclosure todavia no seria publico, y el
    repositorio point-in-time lo filtraria — correctamente.
    """
    fecha_transaccion = hasta - timedelta(days=dias_atras)
    fecha_disclosure = fecha_transaccion + timedelta(days=dias_retraso)
    for indice in range(miembros):
        sesion.add(
            CongresoTrade(
                miembro=f"Legislador {indice}",
                camara="camara",
                symbol=symbol,
                tipo=tipo,
                monto_min=Decimal("15000"),
                monto_max=Decimal("50000"),
                fecha_transaccion=fecha_transaccion,
                fecha_disclosure=fecha_disclosure,
                observed_at=momento(fecha_disclosure),
                dias_retraso=dias_retraso,
            )
        )
    await sesion.flush()
