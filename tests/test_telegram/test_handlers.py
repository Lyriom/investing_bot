"""Tests de los handlers, ejercitados de punta a punta contra la base.

Se usan dobles minimos en lugar de objetos reales de python-telegram-bot: los
handlers solo tocan `update.effective_chat` y `context.bot.send_message`, y
construir un `Update` completo no probaria nada mas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos import CLAVE_CHAT_VINCULADO, EstadoSistema, Ticker
from investing_bot.telegram.handlers import (
    comando_estado,
    comando_start,
    registrar_intento_no_autorizado,
)

CHAT_AUTORIZADO = 123456789


@dataclass
class BotFalso:
    """Captura los mensajes en vez de enviarlos a Telegram."""

    enviados: list[dict[str, Any]] = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str) -> None:
        self.enviados.append({"chat_id": chat_id, "text": text})


@dataclass
class ChatFalso:
    id: int
    type: str = "private"


@dataclass
class UpdateFalso:
    effective_chat: ChatFalso | None


@dataclass
class ContextoFalso:
    bot: BotFalso


def _contexto() -> ContextoFalso:
    return ContextoFalso(bot=BotFalso())


async def test_start_vincula_el_chat_y_lo_persiste(motor: object, sesion: AsyncSession) -> None:
    contexto = _contexto()
    await comando_start(UpdateFalso(ChatFalso(CHAT_AUTORIZADO)), contexto)  # type: ignore[arg-type]

    registro = await sesion.get(EstadoSistema, CLAVE_CHAT_VINCULADO)
    assert registro is not None
    assert registro.valor == {"chat_id": CHAT_AUTORIZADO}

    assert len(contexto.bot.enviados) == 1
    assert contexto.bot.enviados[0]["chat_id"] == CHAT_AUTORIZADO
    assert "NO ejecuta ordenes" in contexto.bot.enviados[0]["text"]


async def test_start_dos_veces_no_duplica_el_vinculo(motor: object, sesion: AsyncSession) -> None:
    await comando_start(UpdateFalso(ChatFalso(CHAT_AUTORIZADO)), _contexto())  # type: ignore[arg-type]
    await comando_start(UpdateFalso(ChatFalso(999)), _contexto())  # type: ignore[arg-type]

    registro = await sesion.get(EstadoSistema, CLAVE_CHAT_VINCULADO)
    assert registro is not None
    await sesion.refresh(registro)
    assert registro.valor == {"chat_id": 999}


async def test_estado_reporta_los_datos_reales(motor: object, sesion: AsyncSession) -> None:
    sesion.add(Ticker(symbol="AAPL", nombre="Apple Inc.", en_whitelist=True))
    await sesion.commit()

    contexto = _contexto()
    await comando_estado(UpdateFalso(ChatFalso(CHAT_AUTORIZADO)), contexto)  # type: ignore[arg-type]

    texto = contexto.bot.enviados[0]["text"]
    assert "Tickers en whitelist   : 1" in texto
    assert "sin corridas todavia" in texto


async def test_los_handlers_ignoran_updates_sin_chat(motor: object) -> None:
    """Un update sin chat no debe reventar el bot."""
    contexto = _contexto()
    await comando_start(UpdateFalso(None), contexto)  # type: ignore[arg-type]
    await comando_estado(UpdateFalso(None), contexto)  # type: ignore[arg-type]
    assert contexto.bot.enviados == []


async def test_el_intruso_recibe_silencio() -> None:
    """SPEC 6.5: cualquier otro chat recibe silencio, no un mensaje de error."""
    contexto = _contexto()
    await registrar_intento_no_autorizado(UpdateFalso(ChatFalso(666)), contexto)  # type: ignore[arg-type]
    assert contexto.bot.enviados == []
