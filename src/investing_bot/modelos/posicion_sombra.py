"""Portafolio simulado de la FASE 4: el sistema sugiere, nadie invierte nada."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, MarcaTiempo, ahora_utc


class PosicionSombra(Base):
    """Posicion abierta en el portafolio sombra. Sin dinero real de por medio."""

    __tablename__ = "posiciones_sombra"
    __table_args__ = (sa.Index("ix_sombra_symbol_abierta", "symbol", "abierta"),)

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False
    )
    fecha_entrada: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    precio_entrada: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6), nullable=False)
    tamano_usd: Mapped[Decimal] = mapped_column(sa.Numeric(18, 2), nullable=False)
    stop: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    fecha_salida: Mapped[date | None] = mapped_column(sa.Date)
    precio_salida: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    motivo_salida: Mapped[str | None] = mapped_column(sa.String(60))
    abierta: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<PosicionSombra {self.symbol} abierta={self.abierta}>"
