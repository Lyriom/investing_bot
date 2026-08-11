"""Construccion y arranque del bot de Telegram."""

from __future__ import annotations

import asyncio

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from investing_bot.config import Configuracion, obtener_config
from investing_bot.registro import obtener_logger
from investing_bot.telegram.handlers import (
    REG_ACCION,
    REG_CANTIDAD,
    REG_NOTAS,
    REG_PRECIO,
    REG_SYMBOL,
    comando_desglose,
    comando_estado,
    comando_hoy,
    comando_pausar,
    comando_reanudar,
    comando_start,
    registrar_accion,
    registrar_cancelar,
    registrar_cantidad,
    registrar_inicio,
    registrar_intento_no_autorizado,
    registrar_notas,
    registrar_precio,
    registrar_symbol,
)

log = obtener_logger(__name__)

COMANDOS = {
    "start": comando_start,
    "estado": comando_estado,
    "hoy": comando_hoy,
    "desglose": comando_desglose,
    "pausar": comando_pausar,
    "reanudar": comando_reanudar,
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

    for nombre, handler in COMANDOS.items():
        aplicacion.add_handler(CommandHandler(nombre, handler, filters=filtro_autorizado))

    # /registrar es conversacional: pregunta ticker, accion, precio, cantidad
    # y notas, uno por uno. El filtro de chat va en cada paso, no solo en la
    # entrada, para que nadie pueda colarse a mitad de la conversacion.
    texto_autorizado = filtro_autorizado & filters.TEXT & ~filters.COMMAND
    aplicacion.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("registrar", registrar_inicio, filters=filtro_autorizado)],
            states={
                REG_SYMBOL: [MessageHandler(texto_autorizado, registrar_symbol)],
                REG_ACCION: [MessageHandler(texto_autorizado, registrar_accion)],
                REG_PRECIO: [MessageHandler(texto_autorizado, registrar_precio)],
                REG_CANTIDAD: [MessageHandler(texto_autorizado, registrar_cantidad)],
                REG_NOTAS: [MessageHandler(texto_autorizado, registrar_notas)],
            },
            fallbacks=[CommandHandler("cancelar", registrar_cancelar, filters=filtro_autorizado)],
        )
    )

    aplicacion.add_handler(
        MessageHandler(~filtro_autorizado, registrar_intento_no_autorizado),
        group=1,
    )
    return aplicacion


def avisar_si_no_configurado(config: Configuracion) -> bool:
    """Registra por que el bot no puede arrancar. Devuelve True si puede."""
    if config.telegram_configurado:
        return True
    log.warning(
        "bot_no_configurado",
        motivo="Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID_AUTORIZADO en el entorno.",
        accion="El servicio termina sin error; el resto del sistema sigue funcionando.",
    )
    return False


async def correr_bot(detener: asyncio.Event, config: Configuracion | None = None) -> None:
    """Arranca el polling y lo sostiene hasta que se pida el apagado.

    `Application.run_polling()` monta y desmonta su propio bucle de eventos, asi
    que no sirve cuando el bot comparte proceso con la API y el planificador
    (modo `todo`). Aqui se maneja el ciclo de vida a mano sobre el bucle que ya
    existe.
    """
    config = config or obtener_config()
    aplicacion = construir_aplicacion(config)
    actualizador = aplicacion.updater
    if actualizador is None:  # pragma: no cover - ApplicationBuilder siempre lo crea
        raise RuntimeError("La aplicacion de Telegram se construyo sin updater.")

    await aplicacion.initialize()
    await aplicacion.start()
    await actualizador.start_polling(drop_pending_updates=True)
    log.info("bot_iniciado", chat_autorizado=config.telegram_chat_id_autorizado)

    try:
        await detener.wait()
    finally:
        # El orden importa: primero se deja de leer updates, luego se para el
        # despachador y al final se sueltan las conexiones HTTP.
        if actualizador.running:
            await actualizador.stop()
        if aplicacion.running:
            await aplicacion.stop()
        await aplicacion.shutdown()
        log.info("bot_detenido")


def ejecutar_bot() -> None:
    """Arranca el bot como servicio suelto. Bloquea el proceso."""
    config = obtener_config()
    if not avisar_si_no_configurado(config):
        return

    aplicacion = construir_aplicacion(config)
    log.info("bot_iniciado", chat_autorizado=config.telegram_chat_id_autorizado)
    aplicacion.run_polling(drop_pending_updates=True)
