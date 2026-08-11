"""Agregado diario de menciones en Reddit."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, MarcaTiempo, ahora_utc


class RedditDiario(Base):
    """Menciones y sentimiento de un ticker en un subreddit, por dia."""

    __tablename__ = "reddit_diario"
    __table_args__ = (
        sa.UniqueConstraint("symbol", "fecha", "subreddit", name="uq_reddit_symbol_fecha_sub"),
        sa.Index("ix_reddit_symbol_observed", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    subreddit: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    menciones: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    sentimiento_promedio: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 4))
    upvotes_totales: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(MarcaTiempo, nullable=False, index=True)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<RedditDiario {self.symbol} {self.fecha} r/{self.subreddit} n={self.menciones}>"
