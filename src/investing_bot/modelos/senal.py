"""Score diario por ticker y su desglose auditable."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, JsonPortable, MarcaTiempo, ahora_utc


class Senal(Base):
    """Score compuesto de un ticker en una fecha.

    `componentes` guarda el aporte numerico de cada senal y `version_modelo`
    la version de pesos que lo produjo. Sin esas dos columnas seria imposible
    reconstruir seis meses despues por que el sistema dijo lo que dijo
    (invariante I3).
    """

    __tablename__ = "senales"
    __table_args__ = (
        sa.UniqueConstraint(
            "symbol", "fecha", "version_modelo", name="uq_senales_symbol_fecha_ver"
        ),
        sa.Index("ix_senales_fecha_score", "fecha", "score_total"),
    )

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    score_total: Mapped[Decimal] = mapped_column(sa.Numeric(8, 4), nullable=False)
    componentes: Mapped[dict[str, Any]] = mapped_column(JsonPortable, nullable=False, default=dict)
    regimen_mercado: Mapped[str | None] = mapped_column(sa.String(20))  # alcista | riesgo
    version_modelo: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<Senal {self.symbol} {self.fecha} score={self.score_total}>"
