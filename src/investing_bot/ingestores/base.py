"""Contrato comun de los ingestores.

Requisitos que todo ingestor debe cumplir (SPEC 6.1):

- Idempotente: correr dos veces el mismo dia no duplica filas.
- Tolerante a fallo: si una fuente cae, se registra y las demas siguen.
  Nunca una excepcion de un ingestor tumba el pipeline.
- Consciente del rate limit: backoff exponencial con jitter.
- Escribe `observed_at` (ver la nota del modelo `PrecioDiario` sobre el unico
  caso donde ese valor no es `now()`).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.db import ahora_utc, obtener_fabrica_sesiones
from investing_bot.modelos.corrida_ingesta import CorridaIngesta
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)


@dataclass(slots=True)
class ResultadoIngesta:
    """Resumen de una corrida de ingesta."""

    ingestor: str
    filas_leidas: int = 0
    filas_nuevas: int = 0
    filas_actualizadas: int = 0
    filas_sin_cambios: int = 0
    errores: list[str] = field(default_factory=list)
    exito: bool = True
    duracion_seg: float = 0.0

    def resumen(self) -> str:
        return (
            f"{self.ingestor}: leidas={self.filas_leidas} nuevas={self.filas_nuevas} "
            f"actualizadas={self.filas_actualizadas} errores={len(self.errores)}"
        )


class Ingestor(ABC):
    """Clase base de todo ingestor."""

    nombre: ClassVar[str] = "base"

    def __init__(self, fabrica_sesiones: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._fabrica = fabrica_sesiones

    @property
    def fabrica(self) -> async_sessionmaker[AsyncSession]:
        """Fabrica de sesiones a usar (inyectable para tests)."""
        return self._fabrica if self._fabrica is not None else obtener_fabrica_sesiones()

    @abstractmethod
    async def ejecutar(self) -> ResultadoIngesta:
        """Hace el trabajo real. Puede lanzar excepciones."""

    async def ejecutar_registrado(self) -> ResultadoIngesta:
        """Envuelve `ejecutar`, captura cualquier fallo y lo deja en la BD.

        Nunca propaga excepciones: el planificador debe poder seguir con los
        demas ingestores pase lo que pase con este.
        """
        inicio_reloj = time.perf_counter()
        iniciado_at = ahora_utc()
        try:
            resultado = await self.ejecutar()
        except Exception as exc:  # noqa: BLE001 - tolerancia a fallo es el punto
            log.exception("ingestor_fallo", ingestor=self.nombre)
            resultado = ResultadoIngesta(
                ingestor=self.nombre,
                exito=False,
                errores=[f"{type(exc).__name__}: {exc}"],
            )

        resultado.duracion_seg = round(time.perf_counter() - inicio_reloj, 3)
        await self._registrar_corrida(resultado, iniciado_at)
        log.info("ingesta_finalizada", **{"resumen": resultado.resumen(), "exito": resultado.exito})
        return resultado

    async def _registrar_corrida(self, resultado: ResultadoIngesta, iniciado_at: datetime) -> None:
        """Deja constancia de la corrida en `corridas_ingesta`.

        Usa su propia sesion para que el registro sobreviva aunque la
        transaccion de la ingesta haya hecho rollback.
        """
        try:
            async with self.fabrica() as sesion:
                sesion.add(
                    CorridaIngesta(
                        ingestor=resultado.ingestor,
                        iniciado_at=iniciado_at,
                        finalizado_at=ahora_utc(),
                        duracion_seg=resultado.duracion_seg,
                        exito=resultado.exito,
                        filas_leidas=resultado.filas_leidas,
                        filas_nuevas=resultado.filas_nuevas,
                        filas_actualizadas=resultado.filas_actualizadas,
                        errores={"mensajes": resultado.errores} if resultado.errores else None,
                    )
                )
                await sesion.commit()
        except Exception:  # noqa: BLE001 - no poder registrar no debe tumbar nada
            log.exception("no_se_pudo_registrar_corrida", ingestor=resultado.ingestor)
