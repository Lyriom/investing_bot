"""Ingestor de noticias (Finnhub free tier).

Cada noticia pasa por tres filtros antes de guardarse: se le atribuye un
ticker (o se descarta), se comprueba que no sea replica de otra ya conocida,
y se clasifica su sentimiento. Sin el segundo filtro, un cable de agencia
replicado por diez portales contaria diez veces (SPEC 6.2).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.config import obtener_config
from investing_bot.db import ahora_utc
from investing_bot.ingestores.base import Ingestor, ResultadoIngesta
from investing_bot.ingestores.utilidades import con_reintentos
from investing_bot.modelos.noticia import Noticia
from investing_bot.modelos.ticker import Ticker
from investing_bot.nlp.sentimiento import ClasificadorSentimiento
from investing_bot.normalizador.deduplicador import buscar_original, hash_titular
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

URL_FINNHUB = "https://finnhub.io/api/v1/company-news"

# El free tier permite 60 llamadas por minuto. Con 30 tickers vamos muy por
# debajo, pero la pausa evita rafagas que disparen el limitador.
PAUSA_ENTRE_TICKERS_SEG = 1.1


async def descargar_finnhub(
    symbol: str, desde: str, hasta: str, api_key: str, timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Trae las noticias de un ticker en un rango de fechas."""
    parametros = {"symbol": symbol, "from": desde, "to": hasta, "token": api_key}
    async with httpx.AsyncClient(timeout=timeout) as cliente:
        respuesta = await cliente.get(URL_FINNHUB, params=parametros)
        respuesta.raise_for_status()
        datos = respuesta.json()
    return datos if isinstance(datos, list) else []


class IngestorNoticias(Ingestor):
    """Trae noticias por ticker, las deduplica y las clasifica."""

    nombre: ClassVar[str] = "noticias"

    def __init__(
        self,
        fabrica_sesiones: async_sessionmaker[AsyncSession] | None = None,
        dias: int | None = None,
        descargador: Any = None,
        clasificador: ClasificadorSentimiento | None = None,
        pausa_seg: float = PAUSA_ENTRE_TICKERS_SEG,
    ) -> None:
        super().__init__(fabrica_sesiones)
        config = obtener_config()
        self.dias = dias if dias is not None else config.dias_historial_noticias
        self.api_key = config.finnhub_api_key
        self.reintentos = config.reintentos_max
        self.backoff_base_seg = config.backoff_base_seg
        self.pausa_seg = pausa_seg
        self._descargador = descargador or descargar_finnhub
        self._clasificador = clasificador or ClasificadorSentimiento.instancia()

    async def ejecutar(self) -> ResultadoIngesta:
        resultado = ResultadoIngesta(ingestor=self.nombre)

        if not self.api_key:
            resultado.exito = False
            resultado.errores.append(
                "FINNHUB_API_KEY vacia. Consigue una gratis en finnhub.io/register "
                "y ponla en el entorno; sin ella no hay senal de deriva post-noticia."
            )
            return resultado

        async with self.fabrica() as sesion:
            symbols = list(
                (
                    await sesion.scalars(
                        sa.select(Ticker.symbol)
                        .where(Ticker.en_whitelist.is_(True), Ticker.activo.is_(True))
                        .order_by(Ticker.symbol)
                    )
                ).all()
            )

        if not symbols:
            resultado.exito = False
            resultado.errores.append("No hay tickers en la whitelist.")
            return resultado

        hasta = ahora_utc().date()
        desde = hasta - timedelta(days=self.dias)

        for indice, symbol in enumerate(symbols):
            if indice:
                await asyncio.sleep(self.pausa_seg)
            try:
                crudas = await con_reintentos(
                    lambda s=symbol: self._descargador(  # type: ignore[misc]
                        s, desde.isoformat(), hasta.isoformat(), self.api_key
                    ),
                    intentos=self.reintentos,
                    base_seg=self.backoff_base_seg,
                    descripcion=f"noticias de {symbol}",
                )
            except Exception as exc:  # noqa: BLE001 - un ticker caido no tumba el resto
                log.warning("noticias_ticker_fallo", symbol=symbol, error=str(exc))
                resultado.errores.append(f"{symbol}: {type(exc).__name__}")
                continue

            resultado.filas_leidas += len(crudas)
            nuevas, duplicadas = await self._guardar(symbol, crudas)
            resultado.filas_nuevas += nuevas
            resultado.filas_sin_cambios += duplicadas

        resultado.exito = resultado.filas_nuevas > 0 or not resultado.errores
        return resultado

    async def _guardar(self, symbol: str, crudas: Sequence[dict[str, Any]]) -> tuple[int, int]:
        """Guarda las noticias nuevas de un ticker. Devuelve (nuevas, ya conocidas)."""
        observed_at = ahora_utc()
        nuevas = 0
        conocidas = 0

        async with self.fabrica() as sesion:
            pendientes: list[Noticia] = []
            for cruda in crudas:
                titulo = (cruda.get("headline") or "").strip()
                if not titulo:
                    continue

                marca = cruda.get("datetime")
                if not marca:
                    continue
                event_at = datetime.fromtimestamp(int(marca), tz=UTC)

                huella = hash_titular(titulo)
                ya_existe = await sesion.scalar(
                    sa.select(Noticia.id).where(Noticia.hash_contenido == huella).limit(1)
                )
                if ya_existe is not None:
                    conocidas += 1
                    continue

                original = await buscar_original(sesion, titulo, event_at)
                noticia = Noticia(
                    symbol=symbol,
                    titulo=titulo,
                    resumen=(cruda.get("summary") or None),
                    url=(cruda.get("url") or None),
                    fuente=(cruda.get("source") or None),
                    hash_contenido=huella,
                    event_at=event_at,
                    observed_at=observed_at,
                    es_duplicado=original is not None,
                    id_original=original.id if original is not None else None,
                )
                sesion.add(noticia)
                pendientes.append(noticia)
                nuevas += 1

            if pendientes:
                await self._clasificar(pendientes)
                await sesion.commit()

        return nuevas, conocidas

    async def _clasificar(self, noticias: Sequence[Noticia]) -> None:
        """Asigna sentimiento y confianza. Solo a las que no son replicas.

        Clasificar una replica seria gastar computo en un dato que despues se
        va a ignorar, y ademas invitaria a contarlo dos veces por descuido.
        """
        originales = [n for n in noticias if not n.es_duplicado]
        if not originales:
            return

        textos = [f"{n.titulo}. {n.resumen or ''}".strip() for n in originales]
        salidas = await asyncio.to_thread(self._clasificador.clasificar_lote, textos)
        for noticia, salida in zip(originales, salidas, strict=True):
            noticia.sentimiento = Decimal(f"{salida.valor:.4f}")
            noticia.confianza = Decimal(f"{salida.confianza:.4f}")
            noticia.modelo_usado = salida.modelo
