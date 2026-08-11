"""Bitacora de ejecuciones de los ingestores.

No esta en la seccion 5 del SPEC, pero la vista de "estado del pipeline"
(seccion 6.7) necesita saber cuando corrio cada ingestor, cuantas filas trajo
y que fallo. Sin esta tabla, el dashboard tendria que inventarselo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, JsonPortable, MarcaTiempo, ahora_utc


class CorridaIngesta(Base):
    """Resultado de una ejecucion de un ingestor."""

    __tablename__ = "corridas_ingesta"
    __table_args__ = (sa.Index("ix_corridas_ingestor_inicio", "ingestor", "iniciado_at"),)

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    ingestor: Mapped[str] = mapped_column(sa.String(40), nullable=False, index=True)
    iniciado_at: Mapped[datetime] = mapped_column(MarcaTiempo, nullable=False)
    finalizado_at: Mapped[datetime | None] = mapped_column(MarcaTiempo)
    duracion_seg: Mapped[float | None] = mapped_column(sa.Float)
    exito: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    filas_leidas: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    filas_nuevas: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    filas_actualizadas: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    errores: Mapped[dict[str, Any] | None] = mapped_column(JsonPortable)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<CorridaIngesta {self.ingestor} exito={self.exito}>"
