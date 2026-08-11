"""Construccion y arranque del bot de Telegram."""

from __future__ import annotations

from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from investing_bot.config import Configuracion, obtener_config
from investing_bot.registro import obtener_logger
from investing_bot.telegram.handlers import (
    comando_estado,
    comando_start,
    registrar_intento_no_autorizado,
)

log = obtener_logger(__name__)

# Comandos disponibles en FASE 0. Los demas (/hoy, /desglose, /registrar,
# /pausar, /reanudar) dependen de senales y sugerencias: FASE 3.
COMANDOS_FASE_0 = {
    "start": comando_start,
    "estado": comando_estado,
}


def construir_aplicacion(config: Configuracion | None = None) -> Application:
    """Arma la aplicacion de python-telegram-bot con la whitelist aplicada.

    Cada handler lleva el filtro de chat autorizado. Lo que no calce con ese
    filtro cae en el handler de registro, que anota el intento y no responde.
    """
    config = config or obtener_config()
    if not config.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN vacio: no se puede construir el bot.")
    if config.telegram_chat_id_autorizado == 0:
        raise ValueError(
            "TELEGRAM_CHAT_ID_AUTORIZADO sin configurar: el bot atenderia a cualquiera."
        )

    filtro_autorizado = filters.Chat(chat_id=config.telegram_chat_id_autorizado)
    aplicacion = ApplicationBuilder().token(config.telegram_bot_token).build()

    for nombre, handler in COMANDOS_FASE_0.items():
        aplicacion.add_handler(CommandHandler(nombre, handler, filters=filtro_autorizado))

    aplicacion.add_handler(
        MessageHandler(~filtro_autorizado, registrar_intento_no_autorizado),
        group=1,
    )
    return aplicacion


def ejecutar_bot() -> None:
    """Arranca el bot en modo polling. Bloquea el proceso."""
    config = obtener_config()
    if not config.telegram_configurado:
        log.warning(
            "bot_no_configurado",
            motivo="Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID_AUTORIZADO en el entorno.",
            accion="El servicio termina sin error; el resto del sistema sigue funcionando.",
        )
        return

    aplicacion = construir_aplicacion(config)
    log.info("bot_iniciado", chat_autorizado=config.telegram_chat_id_autorizado)
    aplicacion.run_polling(drop_pending_updates=True)
