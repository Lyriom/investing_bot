"""Estado operativo persistente (clave -> valor).

Necesario para que el chat vinculado por `/start`, el kill switch y el modo
defensivo sobrevivan a un reinicio de contenedor.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from investing_bot.db import Base, JsonPortable, MarcaTiempo, ahora_utc

CLAVE_CHAT_VINCULADO = "chat_vinculado"
CLAVE_ENVIOS_PAUSADOS = "envios_pausados"


class EstadoSistema(Base):
    """Par clave-valor de estado operativo."""

    __tablename__ = "estado_sistema"

    clave: Mapped[str] = mapped_column(sa.String(60), primary_key=True)
    valor: Mapped[dict[str, Any]] = mapped_column(JsonPortable, nullable=False, default=dict)
    actualizado_at: Mapped[datetime] = mapped_column(
        MarcaTiempo, default=ahora_utc, onupdate=ahora_utc, nullable=False
    )

    def __repr__(self) -> str:
        return f"<EstadoSistema {self.clave}={self.valor}>"
