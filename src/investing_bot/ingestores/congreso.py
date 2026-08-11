"""Ingestor de operaciones divulgadas por el Congreso de EE.UU.

Fuente: los datasets publicos de house-stock-watcher y senate-stock-watcher,
que agregan los formularios PTR de la STOCK Act. No requieren clave.

Lo importante de este ingestor no son los datos, es la disciplina temporal:
la ley concede 45 dias para reportar, asi que `fecha_transaccion` puede ser
mes y medio anterior a `fecha_disclosure`. Confundirlas produce backtests
espectaculares y completamente falsos (invariante I1).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.config import obtener_config
from investing_bot.db import ahora_utc
from investing_bot.ingestores.base import Ingestor, ResultadoIngesta
from investing_bot.ingestores.utilidades import con_reintentos, upsert_filas
from investing_bot.modelos.congreso import CongresoTrade
from investing_bot.modelos.ticker import Ticker
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

# Valores por defecto; se sobreescriben desde la configuracion.
URL_CAMARA = (
    "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
)
URL_SENADO = (
    "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
)

# Plazo legal de la STOCK Act. Pasado eso, la presentacion es tardia.
DIAS_PLAZO_LEGAL = 45

COLUMNAS_CLAVE = ("miembro", "symbol", "fecha_transaccion", "monto_min", "tipo")
COLUMNAS_ACTUALIZAR = (
    "camara",
    "partido",
    "estado",
    "descripcion_activo",
    "monto_max",
    "fecha_disclosure",
    "dias_retraso",
    "presentacion_tardia",
    "url_filing",
)

_MONTO = re.compile(r"\$?\s*([\d,]+)")


def normalizar_tipo(bruto: str | None) -> str | None:
    """Traduce el tipo de operacion de la fuente al vocabulario del sistema.

    Devuelve None si no se reconoce: preferimos perder una fila antes que
    registrarla como compra cuando era otra cosa.
    """
    if not bruto:
        return None
    texto = bruto.strip().lower()
    if "exchange" in texto:
        return "intercambio"
    if "purchase" in texto or texto == "buy":
        return "compra"
    if "sale" in texto or "sold" in texto or texto == "sell":
        return "venta"
    return None


def parsear_rango_monto(bruto: str | None) -> tuple[Decimal | None, Decimal | None]:
    """Extrae el rango de un texto tipo `"$1,001 - $15,000"`.

    Los formularios nunca declaran un monto exacto, solo un tramo. Guardar los
    dos extremos permite ponderar por tamano sin fingir una precision que el
    dato no tiene.
    """
    if not bruto:
        return None, None
    numeros = []
    for encontrado in _MONTO.findall(bruto):
        try:
            numeros.append(Decimal(encontrado.replace(",", "")))
        except InvalidOperation:  # pragma: no cover - texto sin numeros usables
            continue
    if not numeros:
        return None, None
    if len(numeros) == 1:
        return numeros[0], numeros[0]
    return min(numeros), max(numeros)


def parsear_fecha(bruto: str | None) -> date | None:
    """Acepta los formatos que mezclan las dos fuentes."""
    if not bruto:
        return None
    texto = bruto.strip()
    for formato in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _limpiar_symbol(bruto: str | None) -> str | None:
    """Normaliza el ticker. La fuente usa `--` y cadenas vacias para 'ninguno'."""
    if not bruto:
        return None
    symbol = bruto.strip().upper()
    if not symbol or symbol in {"--", "N/A", "NONE"}:
        return None
    return symbol if re.fullmatch(r"[A-Z][A-Z.\-]{0,11}", symbol) else None


def normalizar_operacion(
    bruto: dict[str, Any],
    camara: str,
    symbols_validos: set[str],
    observed_at: datetime,
) -> dict[str, Any] | None:
    """Convierte un registro crudo en una fila de `congreso_trades`.

    Devuelve None cuando la fila no es utilizable. Se descarta en vez de
    rellenar: una operacion sin fecha o sin tipo no aporta nada y ensucia
    el consenso.
    """
    miembro = (bruto.get("representative") or bruto.get("senator") or "").strip()
    tipo = normalizar_tipo(bruto.get("type"))
    fecha_transaccion = parsear_fecha(bruto.get("transaction_date"))
    if not miembro or not tipo or not fecha_transaccion:
        return None

    symbol = _limpiar_symbol(bruto.get("ticker"))
    if symbol is not None and symbol not in symbols_validos:
        # Fuera de la whitelist: se guarda el trade pero sin atar la clave
        # foranea, para no perder el dato sectorial ni romper la integridad.
        symbol = None

    monto_min, monto_max = parsear_rango_monto(bruto.get("amount"))
    fecha_disclosure = parsear_fecha(bruto.get("disclosure_date"))
    dias_retraso = (
        (fecha_disclosure - fecha_transaccion).days if fecha_disclosure is not None else None
    )

    return {
        "miembro": miembro,
        "camara": camara,
        "partido": (bruto.get("party") or None),
        "estado": (bruto.get("state") or None),
        "symbol": symbol,
        "descripcion_activo": (bruto.get("asset_description") or None),
        "tipo": tipo,
        "monto_min": monto_min,
        "monto_max": monto_max,
        "fecha_transaccion": fecha_transaccion,
        "fecha_disclosure": fecha_disclosure,
        "observed_at": observed_at,
        "dias_retraso": dias_retraso,
        "presentacion_tardia": bool(dias_retraso is not None and dias_retraso > DIAS_PLAZO_LEGAL),
        "url_filing": (bruto.get("ptr_link") or None),
    }


async def descargar_json(url: str, timeout: float = 120.0) -> list[dict[str, Any]]:
    """Descarga uno de los datasets publicos."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as cliente:
        respuesta = await cliente.get(url)
        respuesta.raise_for_status()
        datos = respuesta.json()
    return datos if isinstance(datos, list) else []


class IngestorCongreso(Ingestor):
    """Trae los trades divulgados de ambas camaras."""

    nombre: ClassVar[str] = "congreso"

    def __init__(
        self,
        fabrica_sesiones: async_sessionmaker[AsyncSession] | None = None,
        dias: int | None = None,
        descargador: Any = None,
    ) -> None:
        super().__init__(fabrica_sesiones)
        config = obtener_config()
        self.dias = dias if dias is not None else config.dias_historial_congreso
        self.reintentos = config.reintentos_max
        self.backoff_base_seg = config.backoff_base_seg
        self._descargador = descargador or descargar_json
        self.url_camara = config.url_congreso_camara
        self.url_senado = config.url_congreso_senado

    async def _symbols_validos(self, sesion: AsyncSession) -> set[str]:
        return set((await sesion.scalars(sa.select(Ticker.symbol))).all())

    async def ejecutar(self) -> ResultadoIngesta:
        resultado = ResultadoIngesta(ingestor=self.nombre)
        observed_at = ahora_utc()
        corte = observed_at.date() - timedelta(days=self.dias)

        async with self.fabrica() as sesion:
            symbols_validos = await self._symbols_validos(sesion)

        filas: list[dict[str, Any]] = []
        for camara, url in (("camara", self.url_camara), ("senado", self.url_senado)):
            try:
                crudos = await con_reintentos(
                    lambda url=url: self._descargador(url),  # type: ignore[misc]
                    intentos=self.reintentos,
                    base_seg=self.backoff_base_seg,
                    descripcion=f"descarga de {camara}",
                )
            except Exception as exc:  # noqa: BLE001 - una camara caida no tumba la otra
                log.warning("fuente_congreso_fallo", camara=camara, error=str(exc))
                resultado.errores.append(f"{camara}: {type(exc).__name__}: {exc}")
                continue

            filas.extend(self._normalizar_lote(crudos, camara, symbols_validos, observed_at, corte))

        resultado.filas_leidas = len(filas)
        if not filas:
            resultado.exito = not resultado.errores
            if resultado.exito:
                resultado.errores.append("Ninguna operacion en la ventana solicitada.")
            return resultado

        async with self.fabrica() as sesion:
            efecto = await upsert_filas(
                sesion, CongresoTrade, filas, COLUMNAS_CLAVE, COLUMNAS_ACTUALIZAR
            )
            await sesion.commit()

        resultado.filas_nuevas = efecto.nuevas
        resultado.filas_actualizadas = efecto.actualizadas
        resultado.filas_sin_cambios = efecto.sin_cambios
        resultado.exito = True
        return resultado

    def _normalizar_lote(
        self,
        crudos: Iterable[dict[str, Any]],
        camara: str,
        symbols_validos: set[str],
        observed_at: datetime,
        corte: date,
    ) -> list[dict[str, Any]]:
        filas = []
        for bruto in crudos:
            fila = normalizar_operacion(bruto, camara, symbols_validos, observed_at)
            if fila is None or fila["fecha_transaccion"] < corte:
                continue
            filas.append(fila)
        return filas


async def _principal() -> None:  # pragma: no cover - utilidad manual
    resultado = await IngestorCongreso().ejecutar_registrado()
    log.info("resultado", resumen=resultado.resumen())


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_principal())


__all__ = [
    "URL_CAMARA",
    "URL_SENADO",
    "IngestorCongreso",
    "normalizar_operacion",
    "normalizar_tipo",
    "parsear_fecha",
    "parsear_rango_monto",
]
