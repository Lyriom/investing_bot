"""Handlers de comandos del bot.

Seguridad (SPEC 6.5): el bot solo atiende al `chat_id` de la whitelist.
Cualquier otro chat recibe **silencio**, no un mensaje de error: responder
"no autorizado" confirmaria al desconocido que el bot existe y esta vivo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from investing_bot.config import obtener_config
from investing_bot.datos.repositorio_pit import RepositorioPIT
from investing_bot.db import sesion_bd
from investing_bot.modelos.ejecucion import Ejecucion
from investing_bot.modelos.estado_sistema import CLAVE_CHAT_VINCULADO, EstadoSistema
from investing_bot.modelos.sugerencia import Sugerencia
from investing_bot.modelos.ticker import Ticker
from investing_bot.registro import obtener_logger
from investing_bot.senales.motor import MotorSenales
from investing_bot.servicios.consultas import conteos_generales, estado_ingestores
from investing_bot.servicios.digest import estan_pausados, fijar_pausa, generar_digest
from investing_bot.telegram.digest import formatear_desglose
from investing_bot.telegram.formato import formatear_bienvenida, formatear_estado

log = obtener_logger(__name__)

# Estados de la conversacion de /registrar.
REG_SYMBOL, REG_ACCION, REG_PRECIO, REG_CANTIDAD, REG_NOTAS = range(5)


def esta_autorizado(chat_id: int | None, chat_autorizado: int) -> bool:
    """Unica fuente de verdad de la autorizacion del bot."""
    if chat_id is None or chat_autorizado == 0:
        return False
    return chat_id == chat_autorizado


async def _responder(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
    """Envia texto al chat del update. Trocea si excede el limite de Telegram."""
    chat = update.effective_chat
    if chat is None:
        return
    limite = 4000
    for inicio in range(0, len(texto), limite):
        await context.bot.send_message(chat_id=chat.id, text=texto[inicio : inicio + limite])


# --- Comandos basicos ------------------------------------------------------


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
    """/estado — pipeline, datos, regimen de mercado y modo activo."""
    config = obtener_config()
    async with sesion_bd() as sesion:
        conteos = await conteos_generales(sesion)
        ingestores = await estado_ingestores(sesion)
        pausado = await estan_pausados(sesion)
        abiertas = list(
            (
                await sesion.execute(
                    sa.text(
                        "SELECT symbol, precio_entrada, stop FROM posiciones_sombra "
                        "WHERE abierta = true"
                    )
                )
            ).all()
        )

    texto = formatear_estado(
        entorno=config.entorno,
        capital_usd=config.capital_total_usd,
        conteos=conteos,
        ingestores=ingestores,
        zona_operador=config.zona_horaria_operador,
        pausado=pausado,
        posiciones=[(s, float(p), float(st) if st is not None else None) for s, p, st in abiertas],
    )
    await _responder(update, context, texto)


async def comando_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hoy — recalcula y reenvia el digest del dia."""
    async with sesion_bd() as sesion:
        resultado = await generar_digest(sesion, date.today())
    await _responder(update, context, resultado.texto)


async def comando_desglose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/desglose <symbol> — todos los componentes del score de un ticker."""
    argumentos = context.args or []
    if not argumentos:
        await _responder(update, context, "Uso: /desglose NVDA")
        return

    symbol = argumentos[0].upper().strip()
    async with sesion_bd() as sesion:
        ticker = await sesion.get(Ticker, symbol)
        if ticker is None or not ticker.en_whitelist:
            await _responder(update, context, f"{symbol} no esta en la whitelist.")
            return

        fecha = date.today()
        repositorio = RepositorioPIT(sesion, fecha)
        resultado = await MotorSenales().calcular(repositorio, symbol, fecha)

    await _responder(update, context, formatear_desglose(resultado))


async def comando_pausar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pausar — kill switch: deja de enviar sugerencias."""
    async with sesion_bd() as sesion:
        await fijar_pausa(sesion, True)
    log.warning("envios_pausados")
    await _responder(
        update,
        context,
        "Envios PAUSADOS. El pipeline sigue ingiriendo y calculando, pero no se "
        "enviara ningun digest hasta /reanudar.",
    )


async def comando_reanudar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reanudar — vuelve a enviar sugerencias."""
    async with sesion_bd() as sesion:
        await fijar_pausa(sesion, False)
    log.info("envios_reanudados")
    await _responder(update, context, "Envios reanudados.")


# --- /registrar ------------------------------------------------------------


async def registrar_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Arranca el flujo para grabar una ejecucion real."""
    context.user_data.clear()  # type: ignore[union-attr]
    await _responder(
        update,
        context,
        "Registrar una operacion real.\n\nQue ticker? (o /cancelar para salir)",
    )
    return REG_SYMBOL


async def registrar_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    texto = (update.message.text if update.message else "") or ""
    symbol = texto.strip().upper()

    async with sesion_bd() as sesion:
        ticker = await sesion.get(Ticker, symbol)

    if ticker is None:
        await _responder(update, context, f"{symbol} no existe en la base. Prueba otro.")
        return REG_SYMBOL

    context.user_data["symbol"] = symbol  # type: ignore[index]
    await _responder(update, context, "Compra o venta?")
    return REG_ACCION


async def registrar_accion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    texto = ((update.message.text if update.message else "") or "").strip().lower()
    if texto.startswith("c"):
        accion = "comprar"
    elif texto.startswith("v"):
        accion = "vender"
    else:
        await _responder(update, context, "No te entendi. Escribe 'compra' o 'venta'.")
        return REG_ACCION

    context.user_data["accion"] = accion  # type: ignore[index]
    await _responder(update, context, "A que precio se ejecuto? (solo el numero)")
    return REG_PRECIO


def _a_decimal(texto: str) -> Decimal | None:
    try:
        valor = Decimal(texto.strip().replace(",", ".").lstrip("$"))
    except (InvalidOperation, ValueError):
        return None
    return valor if valor > 0 else None


async def registrar_precio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    precio = _a_decimal((update.message.text if update.message else "") or "")
    if precio is None:
        await _responder(update, context, "Precio no valido. Escribe solo el numero, ej: 178.40")
        return REG_PRECIO

    context.user_data["precio"] = precio  # type: ignore[index]
    await _responder(update, context, "Cuantas acciones? (admite fracciones)")
    return REG_CANTIDAD


async def registrar_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cantidad = _a_decimal((update.message.text if update.message else "") or "")
    if cantidad is None:
        await _responder(update, context, "Cantidad no valida. Ej: 0.5")
        return REG_CANTIDAD

    context.user_data["cantidad"] = cantidad  # type: ignore[index]
    await _responder(update, context, "Alguna nota? (o escribe 'no')")
    return REG_NOTAS


async def registrar_notas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    notas = ((update.message.text if update.message else "") or "").strip()
    if notas.lower() in {"no", "-", "ninguna"}:
        notas = ""

    datos = context.user_data or {}
    symbol = datos["symbol"]
    accion = datos["accion"]
    precio = datos["precio"]
    cantidad = datos["cantidad"]

    async with sesion_bd() as sesion:
        # Se ata a la sugerencia mas reciente del ticker, si la hubo. Si no,
        # queda en NULL a proposito: registrar operaciones tomadas sin senal
        # es lo que permite comparar el criterio humano con el del sistema.
        sugerencia_id = await sesion.scalar(
            sa.select(Sugerencia.id)
            .where(Sugerencia.symbol == symbol)
            .order_by(Sugerencia.creado_at.desc())
            .limit(1)
        )
        sesion.add(
            Ejecucion(
                sugerencia_id=sugerencia_id,
                symbol=symbol,
                accion=accion,
                precio_real=precio,
                cantidad=cantidad,
                comisiones=Decimal(str(obtener_config().costo_clearing_usd)),
                fecha=date.today(),
                notas=notas or None,
            )
        )

    log.info("ejecucion_registrada", symbol=symbol, accion=accion)
    total = precio * cantidad
    await _responder(
        update,
        context,
        f"Registrado: {accion} {cantidad} {symbol} @ ${precio} = ${total:.2f}"
        + (
            "\nVinculado a una sugerencia del sistema."
            if sugerencia_id
            else "\nSin sugerencia asociada (decision propia)."
        ),
    )
    context.user_data.clear()  # type: ignore[union-attr]
    return ConversationHandler.END


async def registrar_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()  # type: ignore[union-attr]
    await _responder(update, context, "Registro cancelado.")
    return ConversationHandler.END


# --- Intrusos --------------------------------------------------------------


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
