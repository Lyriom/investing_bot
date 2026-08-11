"""Noticias y su clasificacion de sentimiento."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, IdEntero, MarcaTiempo, ahora_utc


class Noticia(Base):
    """Una noticia atribuida (o no) a un ticker.

    `hash_contenido` es la clave de deduplicacion: diez portales replicando el
    mismo cable de agencia son un dato, no diez. `event_at` es cuando ocurrio
    la publicacion; `observed_at`, cuando el sistema la ingirio (invariante I1).
    """

    __tablename__ = "noticias"
    __table_args__ = (
        sa.UniqueConstraint("hash_contenido", name="uq_noticias_hash"),
        sa.Index("ix_noticias_symbol_observed", "symbol", "observed_at"),
    )

    id: Mapped[int] = mapped_column(IdEntero, primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(
        sa.String(16), sa.ForeignKey("tickers.symbol", ondelete="SET NULL")
    )
    titulo: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resumen: Mapped[str | None] = mapped_column(sa.Text)
    url: Mapped[str | None] = mapped_column(sa.Text)
    fuente: Mapped[str | None] = mapped_column(sa.String(120), index=True)
    hash_contenido: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    event_at: Mapped[datetime] = mapped_column(MarcaTiempo, nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(MarcaTiempo, nullable=False, index=True)

    # Rango [-1, 1]. Nulo mientras no se haya clasificado.
    sentimiento: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 4))
    # Rango [0, 1].
    confianza: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 4))
    modelo_usado: Mapped[str | None] = mapped_column(sa.String(60))

    es_duplicado: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    id_original: Mapped[int | None] = mapped_column(
        IdEntero, sa.ForeignKey("noticias.id", ondelete="SET NULL")
    )
    creado_at: Mapped[datetime] = mapped_column(MarcaTiempo, default=ahora_utc, nullable=False)

    def __repr__(self) -> str:
        return f"<Noticia {self.symbol} {self.titulo[:40]!r}>"
