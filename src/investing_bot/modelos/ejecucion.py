"""Operaciones reales registradas por el operador."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, MarcaTiempo, ahora_utc


class Ejecucion(Base):
    """Una operacion efectivamente ejecutada, a mano, en el broker.

    `sugerencia_id` es nullable a proposito: permite registrar operaciones
    tomadas sin senal del sistema y comparar, con el tiempo, el criterio
    humano contra el del bot. Ese contraste es uno de los datos mas
    informativos que el proyecto va a producir.
    """

    __tablename__ = "ejecuciones"
    __table_args__ = (sa.Index("ix_ejecuciones_symbol_fecha", "symbol", "fecha"),)

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    sugerencia_id: Mapped[int | None] = mapped_column(
        IdEntero, sa.ForeignKey("sugerencias.id", ondelete="SET NULL")
    )
    symbol: Mapped[str] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False
    )
    accion: Mapped[str] = mapped_column(sa.String(20), nullable=False)  # comprar | vender
    precio_real: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6), nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6), nullable=False)
    comisiones: Mapped[Decimal] = mapped_column(
        sa.Numeric(18, 6), default=Decimal("0"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    notas: Mapped[str | None] = mapped_column(sa.Text)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<Ejecucion {self.accion} {self.symbol} {self.cantidad}@{self.precio_real}>"
