"""Handlers de comandos del bot.

Seguridad (SPEC 6.5): el bot solo atiende al `chat_id` de la whitelist.
Cualquier otro chat recibe **silencio**, no un mensaje de error: responder
"no autorizado" confirmaria al desconocido que el bot existe y esta vivo.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from investing_bot.config import obtener_config
from investing_bot.db import sesion_bd
from investing_bot.modelos.estado_sistema import CLAVE_CHAT_VINCULADO, EstadoSistema
from investing_bot.registro import obtener_logger
from investing_bot.servicios.consultas import conteos_generales, estado_ingestores
from investing_bot.telegram.formato import formatear_bienvenida, formatear_estado

log = obtener_logger(__name__)


def esta_autorizado(chat_id: int | None, chat_autorizado: int) -> bool:
    """Unica fuente de verdad de la autorizacion del bot."""
    if chat_id is None or chat_autorizado == 0:
        return False
    return chat_id == chat_autorizado


async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — vincula el chat autorizado y lo deja persistido."""
    chat = update.effective_chat
    if chat is None:
        return

    async with sesion_bd() as sesion:
        registro = await sesion.get(EstadoSistema, CLAVE_CHAT_VINCULADO)
        if registro is None:
            sesion.add(EstadoSistema(clave=CLAVE_CHAT_VINCULADO, valor={"chat_id": chat.id}))
        else:
            registro.valor = {"chat_id": chat.id}

    log.info("chat_vinculado", chat_id=chat.id)
    await context.bot.send_message(chat_id=chat.id, text=formatear_bienvenida(chat.id))


async def comando_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/estado — estado del pipeline, de los datos y del modo activo."""
    chat = update.effective_chat
    if chat is None:
        return

    config = obtener_config()
    async with sesion_bd() as sesion:
        conteos = await conteos_generales(sesion)
        ingestores = await estado_ingestores(sesion)

    texto = formatear_estado(
        entorno=config.entorno,
        capital_usd=config.capital_total_usd,
        conteos=conteos,
        ingestores=ingestores,
        zona_operador=config.zona_horaria_operador,
    )
    await context.bot.send_message(chat_id=chat.id, text=texto)


async def registrar_intento_no_autorizado(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Deja rastro en los logs de quien escribio sin permiso. No responde nada."""
    chat = update.effective_chat
    log.warning(
        "mensaje_no_autorizado_ignorado",
        chat_id=chat.id if chat else None,
        tipo=chat.type if chat else None,
    )
