"""Ingestor de barras diarias OHLCV desde yfinance."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.config import obtener_config
from investing_bot.ingestores.base import Ingestor, ResultadoIngesta
from investing_bot.ingestores.utilidades import con_reintentos, upsert_filas
from investing_bot.modelos.precio import PrecioDiario
from investing_bot.modelos.ticker import Ticker
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

# Nombres de columna de yfinance -> columnas del modelo.
CAMPOS_YFINANCE: dict[str, str] = {
    "Open": "apertura",
    "High": "maximo",
    "Low": "minimo",
    "Close": "cierre",
    "Adj Close": "cierre_ajustado",
    "Volume": "volumen",
}

COLUMNAS_CLAVE = ("symbol", "fecha")
COLUMNAS_ACTUALIZAR = (
    "apertura",
    "maximo",
    "minimo",
    "cierre",
    "cierre_ajustado",
    "volumen",
    "observed_at",
)

# Margen tras el cierre oficial para dar por consolidada la barra del dia.
MARGEN_CONSOLIDACION = timedelta(hours=1)

Descargador = Callable[[Sequence[str], int], pd.DataFrame]


def momento_observacion(fecha: date, zona_mercado: str) -> datetime:
    """Instante mas temprano en que la barra de `fecha` pudo conocerse.

    Ver la nota de `PrecioDiario`: para precios, `observed_at` se deriva del
    cierre del mercado y no del momento de la ingesta, porque de lo contrario
    toda carga historica quedaria inservible para el backtester.
    """
    cierre = datetime.combine(fecha, time(16, 0), tzinfo=ZoneInfo(zona_mercado))
    return (cierre + MARGEN_CONSOLIDACION).astimezone(UTC)


def _a_decimal(valor: Any) -> Decimal | None:
    """Convierte un valor de pandas a Decimal, o None si no es un numero util."""
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if math.isnan(numero) or math.isinf(numero):
        return None
    return Decimal(f"{numero:.6f}")


def _a_fecha(valor: Any) -> date | None:
    """Extrae la fecha del indice de pandas, que puede venir como Timestamp o date."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    fecha = getattr(valor, "date", None)
    return fecha() if callable(fecha) else None


def _a_entero(valor: Any) -> int | None:
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if math.isnan(numero) or math.isinf(numero):
        return None
    return int(numero)


def aplanar_frame_yfinance(
    frame: pd.DataFrame,
    symbols: Sequence[str],
    zona_mercado: str,
) -> list[dict[str, Any]]:
    """Convierte el DataFrame de yfinance en filas listas para `precios_diarios`.

    Funcion pura: acepta tanto el formato de columnas MultiIndex (varios
    tickers) como el plano (un solo ticker). Las barras sin cierre se
    descartan; nunca se inventa un precio.
    """
    if frame is None or frame.empty:
        return []

    filas: list[dict[str, Any]] = []
    es_multi = isinstance(frame.columns, pd.MultiIndex)

    if es_multi:
        presentes = [s for s in dict.fromkeys(frame.columns.get_level_values(0)) if s in symbols]
    elif len(symbols) == 1:
        presentes = list(symbols)
    else:
        raise ValueError(
            "Frame de columnas planas con mas de un symbol: no se puede atribuir cada columna."
        )

    for symbol in presentes:
        sub = frame[symbol] if es_multi else frame
        if sub is None or sub.empty:
            continue

        for indice, registro in sub.iterrows():
            fecha = _a_fecha(indice)
            if fecha is None:
                continue  # indice que no representa un dia: no se puede atribuir
            cierre = _a_decimal(registro.get("Close"))
            if cierre is None:
                continue  # dia sin datos (feriado, o el ticker aun no cotizaba)

            fila: dict[str, Any] = {
                "symbol": symbol,
                "fecha": fecha,
                "cierre": cierre,
                "observed_at": momento_observacion(fecha, zona_mercado),
            }
            for columna_origen, columna_destino in CAMPOS_YFINANCE.items():
                if columna_destino in ("cierre",):
                    continue
                bruto = registro.get(columna_origen)
                if columna_destino == "volumen":
                    fila["volumen"] = _a_entero(bruto)
                else:
                    fila[columna_destino] = _a_decimal(bruto)

            fila.setdefault("cierre_ajustado", cierre)
            if fila.get("cierre_ajustado") is None:
                fila["cierre_ajustado"] = cierre
            filas.append(fila)

    return filas


def descargar_yfinance(symbols: Sequence[str], dias: int) -> pd.DataFrame:
    """Descarga barras diarias. Bloqueante: se llama dentro de un hilo."""
    import yfinance  # import diferido: yfinance tarda en cargar

    return yfinance.download(
        tickers=list(symbols),
        period=f"{dias}d",
        interval="1d",
        auto_adjust=False,
        actions=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )


class IngestorPrecios(Ingestor):
    """Trae las barras diarias de los tickers de la whitelist."""

    nombre: ClassVar[str] = "precios"

    def __init__(
        self,
        fabrica_sesiones: async_sessionmaker[AsyncSession] | None = None,
        dias: int | None = None,
        symbols: Sequence[str] | None = None,
        descargador: Descargador | None = None,
        reintentos: int | None = None,
        backoff_base_seg: float | None = None,
    ) -> None:
        super().__init__(fabrica_sesiones)
        config = obtener_config()
        self.dias = dias if dias is not None else config.dias_historial_precios_inicial
        self.zona_mercado = config.zona_horaria_mercado
        self.reintentos = reintentos if reintentos is not None else config.reintentos_max
        self.backoff_base_seg = (
            backoff_base_seg if backoff_base_seg is not None else config.backoff_base_seg
        )
        self._symbols = list(symbols) if symbols else None
        self._descargador: Descargador = descargador or descargar_yfinance

    async def _symbols_objetivo(self, sesion: AsyncSession) -> list[str]:
        if self._symbols is not None:
            return self._symbols
        consulta = (
            sa.select(Ticker.symbol)
            .where(Ticker.en_whitelist.is_(True), Ticker.activo.is_(True))
            .order_by(Ticker.symbol)
        )
        return list((await sesion.scalars(consulta)).all())

    async def ejecutar(self) -> ResultadoIngesta:
        resultado = ResultadoIngesta(ingestor=self.nombre)

        async with self.fabrica() as sesion:
            symbols = await self._symbols_objetivo(sesion)

        if not symbols:
            resultado.errores.append(
                "No hay tickers en la whitelist. Corre `investing-bot sembrar`."
            )
            resultado.exito = False
            return resultado

        log.info("descargando_precios", tickers=len(symbols), dias=self.dias)
        frame = await con_reintentos(
            lambda: asyncio.to_thread(self._descargador, symbols, self.dias),
            intentos=self.reintentos,
            base_seg=self.backoff_base_seg,
            descripcion="descarga de precios",
        )

        filas = aplanar_frame_yfinance(frame, symbols, self.zona_mercado)
        resultado.filas_leidas = len(filas)
        if not filas:
            resultado.errores.append("La fuente no devolvio ninguna barra utilizable.")
            resultado.exito = False
            return resultado

        async with self.fabrica() as sesion:
            efecto = await upsert_filas(
                sesion,
                PrecioDiario,
                filas,
                COLUMNAS_CLAVE,
                COLUMNAS_ACTUALIZAR,
            )
            await self._actualizar_metadatos_tickers(sesion, symbols)
            await sesion.commit()

        resultado.filas_nuevas = efecto.nuevas
        resultado.filas_actualizadas = efecto.actualizadas
        resultado.filas_sin_cambios = efecto.sin_cambios
        return resultado

    async def _actualizar_metadatos_tickers(
        self, sesion: AsyncSession, symbols: Sequence[str]
    ) -> None:
        """Refresca `precio_ultimo` y `volumen_promedio_30d` en `tickers`."""
        for symbol in symbols:
            ultimas = (
                await sesion.execute(
                    sa.select(PrecioDiario.cierre, PrecioDiario.volumen)
                    .where(PrecioDiario.symbol == symbol)
                    .order_by(PrecioDiario.fecha.desc())
                    .limit(30)
                )
            ).all()
            if not ultimas:
                continue

            precio_ultimo = ultimas[0][0]
            volumenes = [fila[1] for fila in ultimas if fila[1] is not None]
            promedio = Decimal(sum(volumenes)) / Decimal(len(volumenes)) if volumenes else None
            await sesion.execute(
                sa.update(Ticker)
                .where(Ticker.symbol == symbol)
                .values(precio_ultimo=precio_ultimo, volumen_promedio_30d=promedio)
            )
