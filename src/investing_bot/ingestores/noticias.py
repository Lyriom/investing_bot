"""Ingestor de noticias (Finnhub como fuente principal, Marketaux de respaldo).

Cada noticia pasa por tres filtros antes de guardarse: se le atribuye un
ticker (o se descarta), se comprueba que no sea replica de otra ya conocida,
y se clasifica su sentimiento. Sin el segundo filtro, un cable de agencia
replicado por diez portales contaria diez veces (SPEC 6.2).

Los dos proveedores alimentan **la misma senal** (S1, deriva post-noticia).
Son dos agregadores de titulares: cubren en buena medida las mismas fuentes y
no son independientes entre si. Contarlos como dos evidencias separadas seria
justo el anti-objetivo del SPEC — "no tratar fuentes correlacionadas como
independientes". Marketaux esta aqui por cobertura y resistencia a fallos, no
para sumar peso.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
URL_MARKETAUX = "https://api.marketaux.com/v1/news/all"

# El free tier de Finnhub permite 60 llamadas por minuto. Con 30 tickers vamos
# muy por debajo, pero la pausa evita rafagas que disparen el limitador.
PAUSA_ENTRE_TICKERS_SEG = 1.1

Descargador = Callable[..., Awaitable[list[dict[str, Any]]]]


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


def normalizar_articulo_marketaux(articulo: dict[str, Any]) -> dict[str, Any] | None:
    """Traduce un articulo de Marketaux al formato comun. None si es inservible.

    Marketaux trae su propio `sentiment_score` por entidad y **se descarta a
    proposito**: el sistema clasifica el sentimiento con su propio modelo y
    anota cual uso en `modelo_usado`. Mezclar dos escalas distintas bajo la
    misma columna haria incomparables las filas y romperia la auditabilidad
    del invariante I3.
    """
    titulo = (articulo.get("title") or "").strip()
    publicado = articulo.get("published_at")
    if not titulo or not publicado:
        return None
    try:
        momento = datetime.fromisoformat(str(publicado).replace("Z", "+00:00"))
    except ValueError:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return {
        "headline": titulo,
        "summary": articulo.get("description") or articulo.get("snippet"),
        "url": articulo.get("url"),
        "source": articulo.get("source"),
        "datetime": int(momento.timestamp()),
    }


async def descargar_marketaux(
    symbol: str, desde: str, hasta: str, api_key: str, timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Trae noticias de un ticker desde Marketaux, en el formato comun."""
    parametros = {
        "api_token": api_key,
        "symbols": symbol,
        "published_after": f"{desde}T00:00",
        "published_before": f"{hasta}T23:59",
        "language": "en",
        "filter_entities": "true",
    }
    async with httpx.AsyncClient(timeout=timeout) as cliente:
        respuesta = await cliente.get(URL_MARKETAUX, params=parametros)
        respuesta.raise_for_status()
        datos = respuesta.json()

    crudos = datos.get("data") if isinstance(datos, dict) else None
    if not isinstance(crudos, list):
        return []
    normalizados = [normalizar_articulo_marketaux(art) for art in crudos]
    return [art for art in normalizados if art is not None]


class PresupuestoDiario:
    """Contador de peticiones por dia UTC.

    El plan gratuito de Marketaux da 100 peticiones diarias. Sin un tope, una
    caida prolongada de Finnhub agotaria la cuota en la primera corrida de la
    manana y dejaria al sistema sin noticias el resto del dia, justo cuando
    mas falta hacen.
    """

    def __init__(self, maximo: int) -> None:
        self.maximo = maximo
        self._dia: date | None = None
        self._gastadas = 0

    def consumir(self) -> bool:
        """Reserva una peticion. False si la cuota del dia ya se agoto."""
        hoy = ahora_utc().date()
        if self._dia != hoy:
            self._dia, self._gastadas = hoy, 0
        if self._gastadas >= self.maximo:
            return False
        self._gastadas += 1
        return True

    @property
    def restantes(self) -> int:
        """Peticiones que quedan hoy."""
        if self._dia != ahora_utc().date():
            return self.maximo
        return max(0, self.maximo - self._gastadas)


_presupuesto_marketaux: PresupuestoDiario | None = None


def presupuesto_marketaux() -> PresupuestoDiario:
    """Presupuesto compartido por todas las corridas del proceso.

    El planificador construye un ingestor nuevo en cada ejecucion, asi que el
    contador no puede vivir en la instancia: se reiniciaria cada cuatro horas y
    el limite diario no serviria de nada.
    """
    global _presupuesto_marketaux
    if _presupuesto_marketaux is None:
        _presupuesto_marketaux = PresupuestoDiario(obtener_config().max_peticiones_marketaux_dia)
    return _presupuesto_marketaux


def reiniciar_presupuesto_marketaux() -> None:
    """Descarta el presupuesto en curso. Uso exclusivo de los tests."""
    global _presupuesto_marketaux
    _presupuesto_marketaux = None


@dataclass(frozen=True)
class Proveedor:
    """Una fuente de titulares, con su clave y su cuota."""

    nombre: str
    descargar: Descargador
    api_key: str
    presupuesto: PresupuestoDiario | None = None


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
        proveedores: Sequence[Proveedor] | None = None,
    ) -> None:
        super().__init__(fabrica_sesiones)
        config = obtener_config()
        self.dias = dias if dias is not None else config.dias_historial_noticias
        self.api_key = config.finnhub_api_key
        self.reintentos = config.reintentos_max
        self.backoff_base_seg = config.backoff_base_seg
        self.pausa_seg = pausa_seg
        self._clasificador = clasificador or ClasificadorSentimiento.instancia()
        if proveedores is not None:
            self.proveedores = list(proveedores)
        else:
            self.proveedores = self._proveedores_configurados(config, descargador)

    @staticmethod
    def _proveedores_configurados(config: Any, descargador: Any = None) -> list[Proveedor]:
        """Arma la cadena de proveedores en orden de preferencia.

        Finnhub va primero: da el historial completo del rango pedido, mientras
        que el plan gratuito de Marketaux devuelve tres articulos por peticion.
        """
        proveedores: list[Proveedor] = []
        if config.finnhub_api_key or descargador is not None:
            proveedores.append(
                Proveedor(
                    nombre="finnhub",
                    descargar=descargador or descargar_finnhub,
                    api_key=config.finnhub_api_key,
                )
            )
        if config.marketaux_api_key:
            proveedores.append(
                Proveedor(
                    nombre="marketaux",
                    descargar=descargar_marketaux,
                    api_key=config.marketaux_api_key,
                    presupuesto=presupuesto_marketaux(),
                )
            )
        return proveedores

    async def ejecutar(self) -> ResultadoIngesta:
        resultado = ResultadoIngesta(ingestor=self.nombre)

        if not self.proveedores:
            resultado.exito = False
            resultado.errores.append(
                "Sin fuentes de noticias: FINNHUB_API_KEY y MARKETAUX_API_KEY estan vacias. "
                "Consigue una gratis en finnhub.io/register y ponla en el entorno; "
                "sin ella no hay senal de deriva post-noticia."
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

            crudas, errores = await self._traer(symbol, desde, hasta)
            resultado.errores.extend(errores)
            if not crudas:
                continue

            resultado.filas_leidas += len(crudas)
            nuevas, duplicadas = await self._guardar(symbol, crudas)
            resultado.filas_nuevas += nuevas
            resultado.filas_sin_cambios += duplicadas

        resultado.exito = resultado.filas_nuevas > 0 or not resultado.errores
        return resultado

    async def _traer(
        self, symbol: str, desde: date, hasta: date
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Recorre los proveedores hasta que uno devuelva titulares.

        Solo se anotan errores si **ningun** proveedor entrego nada: que Finnhub
        falle y Marketaux lo cubra no es un fallo de la ingesta, es el respaldo
        haciendo su trabajo.
        """
        errores: list[str] = []
        for proveedor in self.proveedores:
            if proveedor.presupuesto is not None and not proveedor.presupuesto.consumir():
                log.info("presupuesto_agotado", proveedor=proveedor.nombre, symbol=symbol)
                continue
            try:
                crudas = await con_reintentos(
                    lambda p=proveedor: p.descargar(  # type: ignore[misc]
                        symbol, desde.isoformat(), hasta.isoformat(), p.api_key
                    ),
                    intentos=self.reintentos,
                    base_seg=self.backoff_base_seg,
                    descripcion=f"noticias de {symbol} ({proveedor.nombre})",
                )
            except Exception as exc:  # noqa: BLE001 - un ticker caido no tumba el resto
                log.warning(
                    "noticias_ticker_fallo",
                    symbol=symbol,
                    proveedor=proveedor.nombre,
                    error=str(exc),
                )
                errores.append(f"{symbol}/{proveedor.nombre}: {type(exc).__name__}")
                continue

            if crudas:
                if proveedor is not self.proveedores[0]:
                    log.info("noticias_desde_respaldo", symbol=symbol, proveedor=proveedor.nombre)
                return crudas, []

        return [], errores

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
