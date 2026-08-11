"""Carga de la whitelist inicial de instrumentos."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.config import obtener_config
from investing_bot.ingestores.utilidades import ResultadoUpsert, upsert_filas
from investing_bot.modelos.ticker import Ticker
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)


def leer_whitelist(archivo: Path | None = None) -> list[dict[str, Any]]:
    """Lee el archivo semilla y devuelve las filas de `tickers`."""
    ruta = archivo or obtener_config().archivo_whitelist
    contenido = json.loads(Path(ruta).read_text(encoding="utf-8"))
    filas: list[dict[str, Any]] = []
    for entrada in contenido["tickers"]:
        filas.append(
            {
                "symbol": entrada["symbol"].upper().strip(),
                "nombre": entrada.get("nombre"),
                "sector": entrada.get("sector"),
                "industria": entrada.get("industria"),
                "en_whitelist": True,
                "activo": True,
            }
        )
    return filas


async def sembrar_whitelist(
    sesion: AsyncSession,
    archivo: Path | None = None,
) -> ResultadoUpsert:
    """Sincroniza la tabla `tickers` con el archivo semilla.

    El archivo es la fuente de verdad: cualquier ticker que ya no figure en el
    queda con `en_whitelist = False`. Asi nada se sugiere fuera de la
    whitelist vigente sin que haya que borrar filas ni perder su historial.
    """
    filas = leer_whitelist(archivo)
    efecto = await upsert_filas(
        sesion,
        Ticker,
        filas,
        columnas_clave=("symbol",),
        columnas_actualizar=("nombre", "sector", "industria", "en_whitelist", "activo"),
    )

    symbols = [fila["symbol"] for fila in filas]
    await sesion.execute(
        sa.update(Ticker)
        .where(Ticker.symbol.not_in(symbols), Ticker.en_whitelist.is_(True))
        .values(en_whitelist=False)
    )

    log.info(
        "whitelist_sembrada",
        total=len(filas),
        nuevas=efecto.nuevas,
        actualizadas=efecto.actualizadas,
    )
    return efecto
