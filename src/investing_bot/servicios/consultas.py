"""Consultas de lectura compartidas por el dashboard y el bot de Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos.corrida_ingesta import CorridaIngesta
from investing_bot.modelos.ejecucion import Ejecucion
from investing_bot.modelos.precio import PrecioDiario
from investing_bot.modelos.ticker import Ticker

# Ingestores contemplados por el SPEC. Los de la FASE 1 aparecen como
# "pendiente" en el dashboard, en lugar de simplemente no existir.
INGESTORES_ESPERADOS = ("precios", "noticias", "reddit", "congreso")


@dataclass(slots=True)
class EstadoIngestor:
    """Ultima corrida conocida de un ingestor."""

    nombre: str
    implementado: bool
    ultima_corrida: datetime | None = None
    exito: bool | None = None
    filas_nuevas: int = 0
    filas_actualizadas: int = 0
    duracion_seg: float | None = None
    errores: list[str] | None = None


@dataclass(slots=True)
class ResumenTicker:
    """Fila de la tabla de instrumentos del dashboard."""

    symbol: str
    nombre: str | None
    sector: str | None
    precio_ultimo: Decimal | None
    volumen_promedio_30d: Decimal | None
    barras: int
    primera_fecha: date | None
    ultima_fecha: date | None


async def estado_ingestores(sesion: AsyncSession) -> list[EstadoIngestor]:
    """Ultima corrida de cada ingestor, incluidos los aun no implementados."""
    corridas = (
        await sesion.execute(
            sa.select(CorridaIngesta).order_by(CorridaIngesta.iniciado_at.desc()).limit(200)
        )
    ).scalars()

    ultima_por_ingestor: dict[str, CorridaIngesta] = {}
    for corrida in corridas:
        ultima_por_ingestor.setdefault(corrida.ingestor, corrida)

    estados: list[EstadoIngestor] = []
    for nombre in INGESTORES_ESPERADOS:
        ultima = ultima_por_ingestor.get(nombre)
        if ultima is None:
            estados.append(EstadoIngestor(nombre=nombre, implementado=nombre == "precios"))
            continue
        errores = None
        if ultima.errores:
            errores = list(ultima.errores.get("mensajes", []))
        estados.append(
            EstadoIngestor(
                nombre=nombre,
                implementado=True,
                ultima_corrida=ultima.iniciado_at,
                exito=ultima.exito,
                filas_nuevas=ultima.filas_nuevas,
                filas_actualizadas=ultima.filas_actualizadas,
                duracion_seg=ultima.duracion_seg,
                errores=errores,
            )
        )
    return estados


async def resumen_tickers(sesion: AsyncSession) -> list[ResumenTicker]:
    """Instrumentos de la whitelist con la cobertura de precios que tienen."""
    agregado = (
        sa.select(
            PrecioDiario.symbol.label("symbol"),
            sa.func.count().label("barras"),
            sa.func.min(PrecioDiario.fecha).label("primera"),
            sa.func.max(PrecioDiario.fecha).label("ultima"),
        )
        .group_by(PrecioDiario.symbol)
        .subquery()
    )

    filas = (
        await sesion.execute(
            sa.select(Ticker, agregado.c.barras, agregado.c.primera, agregado.c.ultima)
            .outerjoin(agregado, agregado.c.symbol == Ticker.symbol)
            .where(Ticker.en_whitelist.is_(True))
            .order_by(Ticker.symbol)
        )
    ).all()

    return [
        ResumenTicker(
            symbol=ticker.symbol,
            nombre=ticker.nombre,
            sector=ticker.sector,
            precio_ultimo=ticker.precio_ultimo,
            volumen_promedio_30d=ticker.volumen_promedio_30d,
            barras=barras or 0,
            primera_fecha=primera,
            ultima_fecha=ultima,
        )
        for ticker, barras, primera, ultima in filas
    ]


async def conteos_generales(sesion: AsyncSession) -> dict[str, int]:
    """Cuantas filas hay en las tablas que importan para el estado del sistema."""
    return {
        "tickers_whitelist": int(
            await sesion.scalar(
                sa.select(sa.func.count()).select_from(Ticker).where(Ticker.en_whitelist.is_(True))
            )
            or 0
        ),
        "barras_precio": int(
            await sesion.scalar(sa.select(sa.func.count()).select_from(PrecioDiario)) or 0
        ),
        "ejecuciones": int(
            await sesion.scalar(sa.select(sa.func.count()).select_from(Ejecucion)) or 0
        ),
    }


async def ultimos_precios(
    sesion: AsyncSession, symbol: str, limite: int = 30
) -> list[PrecioDiario]:
    """Ultimas barras de un simbolo, de la mas reciente a la mas antigua."""
    consulta = (
        sa.select(PrecioDiario)
        .where(PrecioDiario.symbol == symbol.upper())
        .order_by(PrecioDiario.fecha.desc())
        .limit(limite)
    )
    return list((await sesion.scalars(consulta)).all())
