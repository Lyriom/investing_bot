"""S3 — Consenso del Congreso. Peso 0.15. Horizonte de meses.

**No es** "un legislador compro X, entonces comprar X". Es: cuantos miembros
*distintos* compraron el ticker en 90 dias, neto de ventas, ponderado por monto
y penalizado por la antiguedad del disclosure.

La ley concede 45 dias para reportar. Cuando el dato es publico, el trade
ocurrio hace hasta seis semanas y ya lo procesaron miles de sistemas.
**Esto no es informacion privilegiada, es arqueologia.** Por eso el peso es el
mas bajo de las tres y sirve como sesgo de fondo, no como gatillo.
"""

from __future__ import annotations

import math
from datetime import date
from typing import ClassVar

from investing_bot.datos.repositorio_pit import RepositorioPIT
from investing_bot.senales.base import ComponenteSenal, Senal, sin_datos

VENTANA_DIAS = 90
# Un solo legislador no es consenso. Con menos de esto, la senal no habla.
MIEMBROS_MINIMOS = 2
# Vida media de la penalizacion por antiguedad del disclosure, en dias.
VIDA_MEDIA_RETRASO = 60.0
# Numero de miembros netos que satura la senal.
MIEMBROS_PARA_SATURAR = 5.0


def _peso_por_monto(monto_min: float | None, monto_max: float | None) -> float:
    """Peso logaritmico del tamano de la operacion.

    Los formularios solo declaran tramos, asi que se usa el punto medio. La
    escala es logaritmica porque la diferencia entre 1.000 y 15.000 dolares
    importa mucho mas que entre 1 y 5 millones.
    """
    if monto_min is None and monto_max is None:
        return 1.0
    valores = [v for v in (monto_min, monto_max) if v is not None]
    medio = sum(valores) / len(valores)
    if medio <= 0:
        return 1.0
    # log10(1.000) = 3 -> 1.0 ; log10(1.000.000) = 6 -> 2.0
    return max(0.5, min(2.0, math.log10(medio) / 3.0))


class ConsensoCongreso(Senal):
    """Cuantos miembros distintos se posicionaron, neto y con descuento temporal."""

    nombre: ClassVar[str] = "congreso"

    async def calcular(
        self, repositorio: RepositorioPIT, symbol: str, fecha: date
    ) -> ComponenteSenal:
        trades = await repositorio.congreso(symbol, dias=VENTANA_DIAS)
        if not trades:
            return sin_datos(self.nombre, self.peso, f"Sin trades en {VENTANA_DIAS} dias")

        # Se cuenta por miembro, no por operacion: un legislador que compra en
        # cinco tramos es una sola opinion, no cinco.
        aporte_por_miembro: dict[str, float] = {}
        compradores: set[str] = set()
        vendedores: set[str] = set()
        retraso_maximo = 0

        for trade in trades:
            if trade.tipo not in ("compra", "venta"):
                continue  # los intercambios no expresan direccion

            signo = 1.0 if trade.tipo == "compra" else -1.0
            peso_monto = _peso_por_monto(
                float(trade.monto_min) if trade.monto_min is not None else None,
                float(trade.monto_max) if trade.monto_max is not None else None,
            )

            # Penalizacion por antiguedad: lo que se supo hace tres meses ya
            # lo descontó el mercado.
            dias_desde_disclosure = (
                (fecha - trade.fecha_disclosure).days
                if trade.fecha_disclosure is not None
                else VENTANA_DIAS
            )
            descuento = math.exp(-max(0, dias_desde_disclosure) / VIDA_MEDIA_RETRASO)

            aporte = signo * peso_monto * descuento
            actual = aporte_por_miembro.get(trade.miembro, 0.0)
            # Se queda el aporte de mayor magnitud del miembro, no la suma.
            if abs(aporte) > abs(actual):
                aporte_por_miembro[trade.miembro] = aporte

            (compradores if signo > 0 else vendedores).add(trade.miembro)
            retraso_maximo = max(retraso_maximo, trade.dias_retraso or 0)

        if len(aporte_por_miembro) < MIEMBROS_MINIMOS:
            return sin_datos(
                self.nombre,
                self.peso,
                f"Solo {len(aporte_por_miembro)} miembro(s): un legislador no es consenso",
            )

        neto = sum(aporte_por_miembro.values())
        valor = max(-1.0, min(1.0, math.tanh(neto / MIEMBROS_PARA_SATURAR)))

        direccion = "neto compra" if neto > 0 else "neto venta" if neto < 0 else "neutro"
        return ComponenteSenal(
            nombre=self.nombre,
            valor=valor,
            peso=self.peso,
            datos_suficientes=True,
            resumen=f"{len(aporte_por_miembro)} miembros, {direccion}",
            detalle={
                "miembros_distintos": len(aporte_por_miembro),
                "compradores": sorted(compradores),
                "vendedores": sorted(vendedores),
                "aporte_neto": round(neto, 4),
                "operaciones": len(trades),
                "retraso_maximo_dias": retraso_maximo,
                "advertencia": (
                    "El disclosure llega hasta 45 dias tarde: sesgo de fondo, no un gatillo."
                ),
            },
        )
