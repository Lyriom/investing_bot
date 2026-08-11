"""Barras diarias OHLCV."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, MarcaTiempo, ahora_utc


class PrecioDiario(Base):
    """Cierre diario de un simbolo.

    Sobre `observed_at` (invariante I1): para una barra diaria, el instante mas
    temprano en que el sistema *pudo* conocerla es el cierre oficial del
    mercado de ese dia, no el momento en que se ejecuto la ingesta. Escribir
    `now()` aqui haria inservible cualquier carga historica, porque el
    backtester veria 90 dias de precios con la misma fecha de observacion.

    Por eso `observed_at` se deriva del cierre (16:00 ET + margen de
    consolidacion) y `creado_at` guarda aparte el momento real de la ingesta.
    Los precios no se corrigen retroactivamente, asi que la equivalencia es
    segura; para noticias, Reddit y Congreso NO lo es, y alli si se usa `now()`.
    """

    __tablename__ = "precios_diarios"
    __table_args__ = (
        sa.UniqueConstraint("symbol", "fecha", name="uq_precios_symbol_fecha"),
        sa.Index("ix_precios_symbol_observed", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    apertura: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    maximo: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    minimo: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    cierre: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6), nullable=False)
    cierre_ajustado: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    volumen: Mapped[int | None] = mapped_column(sa.BigInteger)
    observed_at: Mapped[datetime] = mapped_column(MarcaTiempo, nullable=False, index=True)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<PrecioDiario {self.symbol} {self.fecha} cierre={self.cierre}>"
