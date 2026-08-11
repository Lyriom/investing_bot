"""Operaciones bursatiles reportadas por miembros del Congreso de EE.UU."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, MarcaTiempo, ahora_utc


class CongresoTrade(Base):
    """Un trade divulgado bajo la STOCK Act.

    Las tres fechas son distintas y confundirlas es el bug mas caro del
    dominio (invariante I1):

    - `fecha_transaccion`: cuando se ejecuto la operacion (event_at).
    - `fecha_disclosure`:  cuando se hizo publica. La ley concede 45 dias.
    - `observed_at`:       cuando este sistema la ingirio.

    El backtester solo puede filtrar por `observed_at`.
    """

    __tablename__ = "congreso_trades"
    __table_args__ = (
        # `postgresql_nulls_not_distinct` es imprescindible: buena parte de los
        # trades llega sin ticker resuelto (`symbol` nulo), y con la semantica
        # SQL por defecto NULL != NULL, asi que el UNIQUE no dispararia y el
        # ingestor dejaria de ser idempotente justo en las filas mas dificiles.
        # PostgreSQL 15+ lo soporta; sqlite (la suite de tests) no, y por eso
        # el test correspondiente verifica la declaracion, no el motor.
        sa.UniqueConstraint(
            "miembro",
            "symbol",
            "fecha_transaccion",
            "monto_min",
            "tipo",
            name="uq_congreso_trade",
            postgresql_nulls_not_distinct=True,
        ),
        sa.Index("ix_congreso_symbol_observed", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    miembro: Mapped[str] = mapped_column(sa.String(160), nullable=False, index=True)
    camara: Mapped[str | None] = mapped_column(sa.String(20))
    partido: Mapped[str | None] = mapped_column(sa.String(40))
    estado: Mapped[str | None] = mapped_column(sa.String(10))

    symbol: Mapped[str | None] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="SET NULL")
    )
    descripcion_activo: Mapped[str | None] = mapped_column(sa.Text)
    tipo: Mapped[str] = mapped_column(sa.String(20), nullable=False)  # compra | venta | intercambio
    monto_min: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 2))
    monto_max: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 2))

    fecha_transaccion: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    fecha_disclosure: Mapped[date | None] = mapped_column(sa.Date, index=True)
    observed_at: Mapped[datetime] = mapped_column(MarcaTiempo, nullable=False, index=True)

    dias_retraso: Mapped[int | None] = mapped_column(sa.Integer)
    presentacion_tardia: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    url_filing: Mapped[str | None] = mapped_column(sa.Text)
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<CongresoTrade {self.miembro} {self.tipo} {self.symbol} {self.fecha_transaccion}>"
