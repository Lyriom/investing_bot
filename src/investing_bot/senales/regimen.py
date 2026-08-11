"""S4 — Regimen de mercado. Es un veto, no una senal.

Si SPY cierra bajo su media movil de 200 dias -> modo defensivo, sin compras
nuevas.

Regla simple y de las mas valiosas que existen: sin ella, cualquier estrategia
solo-largos se ve excelente en backtest y se destruye en el primer mercado
bajista. No pretende predecir nada; solo apaga la maquina cuando el viento
sopla en contra.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from investing_bot.config import obtener_config
from investing_bot.datos.repositorio_pit import RepositorioPIT

REGIMEN_ALCISTA = "alcista"
REGIMEN_RIESGO = "riesgo"
REGIMEN_DESCONOCIDO = "desconocido"


@dataclass(slots=True)
class EstadoRegimen:
    """Lectura del regimen de mercado en una fecha."""

    regimen: str
    modo_defensivo: bool
    detalle: dict[str, Any]
    resumen: str

    @property
    def hay_datos(self) -> bool:
        return self.regimen != REGIMEN_DESCONOCIDO


async def evaluar_regimen(
    repositorio: RepositorioPIT,
    fecha: date,
    symbol: str | None = None,
    dias_media: int | None = None,
) -> EstadoRegimen:
    """Compara el cierre del indice de referencia contra su media movil.

    Sin suficientes barras el regimen es `desconocido`, y eso **tambien activa
    el modo defensivo**: no saber en que mercado estamos no es razon para
    comprar, es razon para esperar.
    """
    config = obtener_config()
    symbol = symbol or config.symbol_referencia_regimen
    dias_media = dias_media or config.dias_media_movil_regimen

    # Se piden mas dias naturales que barras hacen falta: solo ~252 de los 365
    # dias de un ano son habiles.
    barras = await repositorio.precios(symbol, dias=int(dias_media * 1.6), limite=dias_media)

    if len(barras) < dias_media:
        return EstadoRegimen(
            regimen=REGIMEN_DESCONOCIDO,
            modo_defensivo=True,
            resumen=(
                f"Regimen desconocido: {len(barras)} de {dias_media} barras de {symbol}. "
                "Sin poder medirlo, no se abren posiciones."
            ),
            detalle={
                "symbol": symbol,
                "barras_disponibles": len(barras),
                "barras_necesarias": dias_media,
            },
        )

    cierres = [float(b.cierre) for b in barras]
    cierre_actual = cierres[0]
    media = sum(cierres) / len(cierres)
    distancia = (cierre_actual - media) / media

    alcista = cierre_actual >= media
    return EstadoRegimen(
        regimen=REGIMEN_ALCISTA if alcista else REGIMEN_RIESGO,
        modo_defensivo=not alcista,
        resumen=(f"{symbol} {'sobre' if alcista else 'bajo'} su MA{dias_media} ({distancia:+.1%})"),
        detalle={
            "symbol": symbol,
            "cierre": round(cierre_actual, 4),
            f"ma{dias_media}": round(media, 4),
            "distancia_pct": round(distancia, 4),
            "barras_usadas": len(barras),
        },
    )
