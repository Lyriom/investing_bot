"""Contrato comun de las senales.

Cada senal devuelve un valor en [-1, 1] y el detalle numerico de como llego a
el. Ese detalle no es decorativo: es lo que hace auditable la sugerencia
(invariante I3) y lo que se muestra en `/desglose`.

Si una senal no tiene datos suficientes, aporta **cero**. Nunca se inventa un
valor ni se extrapola: un dato ausente es ausencia de evidencia, no evidencia
en contra.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar

from investing_bot.datos.repositorio_pit import RepositorioPIT


@dataclass(slots=True)
class ComponenteSenal:
    """Aporte de una senal al score de un ticker."""

    nombre: str
    valor: float
    peso: float
    detalle: dict[str, Any] = field(default_factory=dict)
    datos_suficientes: bool = True
    resumen: str = ""

    @property
    def puntos(self) -> float:
        """Aporte en puntos de score. Cero si no habia datos suficientes."""
        if not self.datos_suficientes:
            return 0.0
        return round(self.valor * self.peso * 100, 2)

    def a_dict(self) -> dict[str, Any]:
        """Forma serializable, tal como se guarda en `senales.componentes`."""
        return {
            "valor": round(self.valor, 4),
            "peso": self.peso,
            "puntos": self.puntos,
            "datos_suficientes": self.datos_suficientes,
            "resumen": self.resumen,
            "detalle": self.detalle,
        }


def sin_datos(nombre: str, peso: float, motivo: str) -> ComponenteSenal:
    """Componente neutro, con el motivo explicito de por que no aporta."""
    return ComponenteSenal(
        nombre=nombre,
        valor=0.0,
        peso=peso,
        datos_suficientes=False,
        resumen=motivo,
        detalle={"motivo": motivo},
    )


class Senal(ABC):
    """Clase base de las senales."""

    nombre: ClassVar[str] = "base"

    def __init__(self, peso: float) -> None:
        self.peso = peso

    @abstractmethod
    async def calcular(
        self, repositorio: RepositorioPIT, symbol: str, fecha: date
    ) -> ComponenteSenal:
        """Calcula el aporte de esta senal para un ticker en una fecha.

        Solo puede leer a traves de `repositorio`, que ya acota por
        `observed_at <= fecha`.
        """
