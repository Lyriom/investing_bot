"""El bot solo atiende al chat autorizado. Todo lo demas recibe silencio."""

from __future__ import annotations

import pytest
from telegram.ext import CommandHandler

from investing_bot.config import Configuracion
from investing_bot.telegram.bot import COMANDOS_FASE_0, construir_aplicacion
from investing_bot.telegram.handlers import esta_autorizado

CHAT_AUTORIZADO = 123456789
CHAT_INTRUSO = 987654321


def _config(**extra: object) -> Configuracion:
    base: dict[str, object] = {
        "telegram_bot_token": "123456:token-de-prueba",
        "telegram_chat_id_autorizado": CHAT_AUTORIZADO,
        "_env_file": None,
    }
    base.update(extra)
    return Configuracion(**base)  # type: ignore[arg-type]


def test_solo_el_chat_configurado_esta_autorizado() -> None:
    assert esta_autorizado(CHAT_AUTORIZADO, CHAT_AUTORIZADO)
    assert not esta_autorizado(CHAT_INTRUSO, CHAT_AUTORIZADO)
    assert not esta_autorizado(None, CHAT_AUTORIZADO)


def test_sin_chat_configurado_no_se_autoriza_a_nadie() -> None:
    """Con `TELEGRAM_CHAT_ID_AUTORIZADO=0` el bot no debe atender a nadie."""
    assert not esta_autorizado(CHAT_AUTORIZADO, 0)
    assert not esta_autorizado(0, 0)


def test_no_se_construye_el_bot_sin_token() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        construir_aplicacion(_config(telegram_bot_token=""))


def test_no_se_construye_el_bot_sin_chat_autorizado() -> None:
    """Arrancar con chat 0 significaria atender a cualquiera: se prohibe."""
    with pytest.raises(ValueError, match="TELEGRAM_CHAT_ID_AUTORIZADO"):
        construir_aplicacion(_config(telegram_chat_id_autorizado=0))


def test_cada_comando_lleva_el_filtro_de_chat_autorizado() -> None:
    aplicacion = construir_aplicacion(_config())
    handlers = [h for h in aplicacion.handlers[0] if isinstance(h, CommandHandler)]

    assert {next(iter(h.commands)) for h in handlers} == set(COMANDOS_FASE_0)
    for handler in handlers:
        assert handler.filters is not None
        assert CHAT_AUTORIZADO in handler.filters.chat_ids  # type: ignore[union-attr]
        assert CHAT_INTRUSO not in handler.filters.chat_ids  # type: ignore[union-attr]


def test_fase_0_no_expone_comandos_de_fases_posteriores() -> None:
    """El SPEC manda una fase a la vez: /hoy, /desglose y /registrar son FASE 3."""
    assert set(COMANDOS_FASE_0) == {"start", "estado"}
