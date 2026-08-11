"""Universo de instrumentos. `en_whitelist` es el filtro maestro del sistema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, MarcaTiempo, ahora_utc


class Ticker(Base):
    """Un simbolo negociable (accion o ETF) del mercado de EE.UU.

    Nada se sugiere jamas fuera de la whitelist: `en_whitelist` es el filtro
    maestro que atraviesa todo el pipeline.
    """

    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(sa.String(16), primary_key=True)
    nombre: Mapped[str | None] = mapped_column(sa.String(200))
    sector: Mapped[str | None] = mapped_column(sa.String(100), index=True)
    industria: Mapped[str | None] = mapped_column(sa.String(150))
    volumen_promedio_30d: Mapped[Decimal | None] = mapped_column(sa.Numeric(20, 2))
    precio_ultimo: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    en_whitelist: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, nullable=False, index=True
    )
    activo: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)
    actualizado_at: Mapped[datetime] = mapped_column(
        MarcaTiempo, default=ahora_utc, onupdate=ahora_utc, nullable=False
    )

    def __repr__(self) -> str:
        return f"<Ticker {self.symbol} whitelist={self.en_whitelist}>"
