"""Ingestor de menciones en Reddit.

Agrega por (ticker, dia, subreddit): cuantas veces se menciono, con que
sentimiento medio y cuantos upvotes acumulo.

El dato crudo no vale nada por si mismo; lo que la senal S2 mira despues es
la *aceleracion* de las menciones frente a su media de 30 dias. Por eso aqui
solo se cuenta con cuidado y se deja el juicio para mas adelante.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.config import obtener_config
from investing_bot.db import ahora_utc
from investing_bot.ingestores.base import Ingestor, ResultadoIngesta
from investing_bot.ingestores.utilidades import upsert_filas
from investing_bot.modelos.reddit import RedditDiario
from investing_bot.nlp.sentimiento import ClasificadorSentimiento
from investing_bot.normalizador.entidades import ResolutorEntidades, construir_resolutor
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

COLUMNAS_CLAVE = ("symbol", "fecha", "subreddit")
COLUMNAS_ACTUALIZAR = ("menciones", "sentimiento_promedio", "upvotes_totales", "observed_at")


@dataclass(frozen=True, slots=True)
class Publicacion:
    """Un post de Reddit, reducido a lo que el sistema necesita."""

    subreddit: str
    titulo: str
    texto: str
    upvotes: int
    creado_utc: float

    @property
    def momento(self) -> datetime:
        return datetime.fromtimestamp(self.creado_utc, tz=UTC)

    @property
    def fecha(self) -> date:
        return self.momento.date()


def descargar_praw(
    subreddits: Sequence[str], limite: int, desde: datetime
) -> list[Publicacion]:  # pragma: no cover - requiere credenciales reales
    """Trae los posts recientes de cada subreddit. Bloqueante: va en un hilo."""
    import praw

    config = obtener_config()
    cliente = praw.Reddit(
        client_id=config.reddit_client_id,
        client_secret=config.reddit_client_secret,
        user_agent=config.reddit_user_agent,
        check_for_async=False,
    )

    publicaciones: list[Publicacion] = []
    corte = desde.timestamp()
    for nombre in subreddits:
        for post in cliente.subreddit(nombre).new(limit=limite):
            if post.created_utc < corte:
                break  # `new` viene ordenado: en cuanto uno es viejo, el resto tambien
            publicaciones.append(
                Publicacion(
                    subreddit=nombre,
                    titulo=post.title or "",
                    texto=getattr(post, "selftext", "") or "",
                    upvotes=int(getattr(post, "score", 0) or 0),
                    creado_utc=float(post.created_utc),
                )
            )
    return publicaciones


def agregar_menciones(
    publicaciones: Iterable[Publicacion],
    resolutor: ResolutorEntidades,
    clasificador: ClasificadorSentimiento,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    """Convierte posts en filas de `reddit_diario`.

    Un post que menciona dos tickers cuenta una vez para cada uno. Un post que
    menciona el mismo ticker cinco veces cuenta **una sola vez**: si no, un
    solo autor entusiasta moveria la senal el solo.
    """
    acumulado: dict[tuple[str, date, str], dict[str, Any]] = {}
    por_clasificar: list[tuple[tuple[str, date, str], str]] = []

    for publicacion in publicaciones:
        texto = f"{publicacion.titulo}\n{publicacion.texto}"
        coincidencias = resolutor.resolver(texto)
        if not coincidencias:
            continue

        for coincidencia in coincidencias:
            clave = (coincidencia.symbol, publicacion.fecha, publicacion.subreddit)
            fila = acumulado.setdefault(
                clave,
                {
                    "symbol": coincidencia.symbol,
                    "fecha": publicacion.fecha,
                    "subreddit": publicacion.subreddit,
                    "menciones": 0,
                    "upvotes_totales": 0,
                    "observed_at": observed_at,
                    "_sentimientos": [],
                },
            )
            fila["menciones"] += 1
            fila["upvotes_totales"] += max(0, publicacion.upvotes)
            por_clasificar.append((clave, publicacion.titulo))

    if por_clasificar:
        salidas = clasificador.clasificar_lote([titulo for _, titulo in por_clasificar])
        for (clave, _), salida in zip(por_clasificar, salidas, strict=True):
            acumulado[clave]["_sentimientos"].append(salida.valor)

    filas = []
    for fila in acumulado.values():
        sentimientos = fila.pop("_sentimientos")
        promedio = sum(sentimientos) / len(sentimientos) if sentimientos else None
        fila["sentimiento_promedio"] = Decimal(f"{promedio:.4f}") if promedio is not None else None
        filas.append(fila)
    return filas


class IngestorReddit(Ingestor):
    """Agrega menciones de los tickers de la whitelist en los subreddits."""

    nombre: ClassVar[str] = "reddit"

    def __init__(
        self,
        fabrica_sesiones: async_sessionmaker[AsyncSession] | None = None,
        horas: int | None = None,
        descargador: Any = None,
        clasificador: ClasificadorSentimiento | None = None,
    ) -> None:
        super().__init__(fabrica_sesiones)
        config = obtener_config()
        self.horas = horas if horas is not None else config.horas_ventana_reddit
        self.limite = config.limite_posts_reddit
        self.subreddits = [s.strip() for s in config.subreddits.split(",") if s.strip()]
        self._credenciales = bool(config.reddit_client_id and config.reddit_client_secret)
        self._descargador = descargador or descargar_praw
        self._clasificador = clasificador or ClasificadorSentimiento.instancia()

    async def ejecutar(self) -> ResultadoIngesta:
        resultado = ResultadoIngesta(ingestor=self.nombre)

        if self._descargador is descargar_praw and not self._credenciales:
            resultado.exito = False
            resultado.errores.append(
                "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET vacias. Crea una app de tipo "
                "'script' en reddit.com/prefs/apps; sin ellas no hay senal de Reddit."
            )
            return resultado

        async with self.fabrica() as sesion:
            resolutor = await construir_resolutor(sesion)

        desde = ahora_utc() - timedelta(hours=self.horas)
        publicaciones = await asyncio.to_thread(
            self._descargador, self.subreddits, self.limite, desde
        )
        resultado.filas_leidas = len(publicaciones)

        filas = await asyncio.to_thread(
            agregar_menciones, publicaciones, resolutor, self._clasificador, ahora_utc()
        )
        if not filas:
            resultado.errores.append("Ningun post menciono un ticker de la whitelist.")
            return resultado

        async with self.fabrica() as sesion:
            efecto = await upsert_filas(
                sesion, RedditDiario, filas, COLUMNAS_CLAVE, COLUMNAS_ACTUALIZAR
            )
            await sesion.commit()

        resultado.filas_nuevas = efecto.nuevas
        resultado.filas_actualizadas = efecto.actualizadas
        resultado.filas_sin_cambios = efecto.sin_cambios
        return resultado
