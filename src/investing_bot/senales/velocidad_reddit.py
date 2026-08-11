"""S2 — Velocidad de menciones en Reddit. Peso 0.25. Horizonte 2-10 dias.

Z-score de las menciones del dia contra su media movil de 30 dias, cruzado con
el sentimiento promedio.

Riesgo explicito y por eso el peso esta limitado: en un pump de foro, **el que
llega tarde es la liquidez de salida del que entro antes**. Un pico de
menciones puede ser tanto el principio de un movimiento como el final. El
gestor de riesgo aplica ademas un cooldown por ticker.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date
from typing import ClassVar

from investing_bot.datos.repositorio_pit import RepositorioPIT
from investing_bot.senales.base import ComponenteSenal, Senal, sin_datos

VENTANA_DIAS = 30
# Sin al menos esto no hay media movil creible: un z-score sobre cuatro
# observaciones es un numero con apariencia de rigor y nada detras.
DIAS_MINIMOS = 10
# Divisor del z-score antes de pasarlo por tanh. z=3 -> ~0.76.
ESCALA_Z = 3.0
SENTIMIENTO_MINIMO = 0.05


class VelocidadReddit(Senal):
    """Aceleracion de las menciones frente a su propia normalidad."""

    nombre: ClassVar[str] = "reddit"

    async def calcular(
        self, repositorio: RepositorioPIT, symbol: str, fecha: date
    ) -> ComponenteSenal:
        filas = await repositorio.reddit(symbol, dias=VENTANA_DIAS)
        if not filas:
            return sin_datos(self.nombre, self.peso, "Sin menciones registradas")

        # Se suman los subreddits de un mismo dia: la senal es cuanto se habla
        # del ticker, no en que foro concreto.
        por_dia: dict[date, int] = defaultdict(int)
        sentimiento_por_dia: dict[date, list[float]] = defaultdict(list)
        for fila in filas:
            por_dia[fila.fecha] += fila.menciones
            if fila.sentimiento_promedio is not None:
                sentimiento_por_dia[fila.fecha].append(float(fila.sentimiento_promedio))

        if len(por_dia) < DIAS_MINIMOS:
            return sin_datos(
                self.nombre,
                self.peso,
                f"Solo {len(por_dia)} dias de historial, hacen falta {DIAS_MINIMOS}",
            )

        hoy = max(por_dia)
        menciones_hoy = por_dia[hoy]
        anteriores = [v for f, v in por_dia.items() if f < hoy]
        if len(anteriores) < DIAS_MINIMOS - 1:
            return sin_datos(self.nombre, self.peso, "Historial previo insuficiente")

        media = statistics.fmean(anteriores)
        desviacion = statistics.pstdev(anteriores)
        if desviacion == 0:
            # Serie plana: cualquier cambio daria z infinito. Se descarta.
            return sin_datos(self.nombre, self.peso, "Menciones sin variacion historica")

        z = (menciones_hoy - media) / desviacion
        sentimientos = sentimiento_por_dia.get(hoy, [])
        sentimiento = statistics.fmean(sentimientos) if sentimientos else 0.0

        if abs(sentimiento) < SENTIMIENTO_MINIMO:
            return sin_datos(
                self.nombre,
                self.peso,
                f"z={z:.1f} pero el sentimiento es neutro: no hay direccion",
            )

        # El z-score da la magnitud; el sentimiento, el signo. Un pico de
        # menciones negativas es senal de venta, no de compra.
        magnitud = math.tanh(abs(z) / ESCALA_Z)
        valor = max(-1.0, min(1.0, magnitud * (1 if sentimiento > 0 else -1)))

        return ComponenteSenal(
            nombre=self.nombre,
            valor=valor,
            peso=self.peso,
            datos_suficientes=True,
            resumen=f"z={z:+.1f}, sent {sentimiento:+.2f}, {menciones_hoy} menciones",
            detalle={
                "menciones_hoy": menciones_hoy,
                "media_historica": round(media, 2),
                "desviacion": round(desviacion, 2),
                "z_score": round(z, 3),
                "sentimiento_promedio": round(sentimiento, 4),
                "dias_historial": len(por_dia),
            },
        )
