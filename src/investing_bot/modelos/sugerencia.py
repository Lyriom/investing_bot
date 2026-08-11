"""Sugerencias emitidas al operador. El sistema sugiere; la persona decide."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, MarcaTiempo, ahora_utc


class Sugerencia(Base):
    """Una recomendacion concreta enviada por Telegram.

    Ninguna sugerencia se emite sin stop (`stop_sugerido`) ni sin la senal que
    la origino (`senal_id`): invariante I3.
    """

    __tablename__ = "sugerencias"
    __table_args__ = (sa.Index("ix_sugerencias_symbol_enviada", "symbol", "enviada_at"),)

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    senal_id: Mapped[int | None] = mapped_column(
        IdEntero, sa.ForeignKey("senales.id", ondelete="SET NULL")
    )
    symbol: Mapped[str] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False
    )
    accion: Mapped[str] = mapped_column(sa.String(20), nullable=False)  # comprar|vender|mantener
    precio_referencia: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    stop_sugerido: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    tamano_sugerido_usd: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 2))
    razon: Mapped[str | None] = mapped_column(sa.Text)
    enviada_at: Mapped[datetime | None] = mapped_column(MarcaTiempo, index=True)
    mensaje_telegram_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<Sugerencia {self.accion} {self.symbol}>"
