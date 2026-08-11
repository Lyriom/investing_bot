"""Generacion y envio del digest diario.

Encadena todo el pipeline de decision: scores -> gestor de riesgo ->
sugerencias -> portafolio sombra -> mensaje de Telegram.

Cada sugerencia emitida abre una posicion en el portafolio sombra. No mueve
dinero: sirve para que el gestor de riesgo sepa que ya esta expuesto a ese
ticker y para poder medir despues, con honestidad, que habria pasado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.config import obtener_config
from investing_bot.db import ahora_utc
from investing_bot.modelos.estado_sistema import (
    CLAVE_CHAT_VINCULADO,
    CLAVE_ENVIOS_PAUSADOS,
    EstadoSistema,
)
from investing_bot.modelos.posicion_sombra import PosicionSombra
from investing_bot.modelos.senal import Senal as SenalModelo
from investing_bot.modelos.sugerencia import Sugerencia
from investing_bot.modelos.ticker import Ticker
from investing_bot.registro import obtener_logger
from investing_bot.riesgo.gestor import DecisionRiesgo, GestorRiesgo
from investing_bot.senales.motor import MotorSenales, ResultadoScore
from investing_bot.telegram.digest import formatear_digest

log = obtener_logger(__name__)


@dataclass(slots=True)
class ResultadoDigest:
    """Todo lo que produjo una corrida del digest."""

    fecha: date
    texto: str
    decision: DecisionRiesgo
    scores: list[ResultadoScore]
    sugerencias_guardadas: int = 0
    enviado: bool = False


async def estan_pausados(sesion: AsyncSession) -> bool:
    """Kill switch de `/pausar`."""
    registro = await sesion.get(EstadoSistema, CLAVE_ENVIOS_PAUSADOS)
    return bool(registro and registro.valor.get("pausado"))


async def fijar_pausa(sesion: AsyncSession, pausado: bool) -> None:
    """Activa o desactiva el kill switch."""
    registro = await sesion.get(EstadoSistema, CLAVE_ENVIOS_PAUSADOS)
    if registro is None:
        sesion.add(EstadoSistema(clave=CLAVE_ENVIOS_PAUSADOS, valor={"pausado": pausado}))
    else:
        registro.valor = {"pausado": pausado}
    await sesion.commit()


async def chat_destino(sesion: AsyncSession) -> int | None:
    """Chat al que enviar. El vinculado por `/start`, o el de configuracion."""
    registro = await sesion.get(EstadoSistema, CLAVE_CHAT_VINCULADO)
    if registro is not None:
        chat_id = registro.valor.get("chat_id")
        if chat_id:
            return int(chat_id)
    configurado = obtener_config().telegram_chat_id_autorizado
    return configurado or None


async def generar_digest(sesion: AsyncSession, fecha: date) -> ResultadoDigest:
    """Calcula scores, aplica riesgo y arma el texto. No envia ni persiste nada."""
    config = obtener_config()
    motor = MotorSenales()
    scores = await motor.calcular_whitelist(sesion, fecha)

    tickers = {
        t.symbol: t
        for t in (
            await sesion.scalars(sa.select(Ticker).where(Ticker.en_whitelist.is_(True)))
        ).all()
    }

    gestor = GestorRiesgo(config)
    estado = await gestor.leer_estado(sesion, fecha)
    decision = gestor.evaluar(scores, estado, tickers)

    texto = formatear_digest(fecha, decision, scores, config.umbral_sugerencia)
    return ResultadoDigest(fecha=fecha, texto=texto, decision=decision, scores=scores)


async def persistir_digest(sesion: AsyncSession, resultado: ResultadoDigest) -> int:
    """Guarda los scores, las sugerencias y las posiciones sombra."""
    motor = MotorSenales()
    await motor.persistir(sesion, resultado.scores)

    guardadas = 0
    for propuesta in resultado.decision.propuestas:
        # Se ata la sugerencia a la senal que la produjo: sin ese vinculo no
        # se puede reconstruir despues por que el sistema dijo lo que dijo.
        senal_id = await sesion.scalar(
            sa.select(SenalModelo.id).where(
                SenalModelo.symbol == propuesta.symbol,
                SenalModelo.fecha == resultado.fecha,
                SenalModelo.version_modelo == propuesta.resultado.version_modelo,
            )
        )

        sugerencia = Sugerencia(
            senal_id=senal_id,
            symbol=propuesta.symbol,
            accion=propuesta.accion,
            precio_referencia=Decimal(f"{propuesta.precio_referencia:.6f}"),
            stop_sugerido=Decimal(f"{propuesta.stop:.6f}"),
            tamano_sugerido_usd=Decimal(f"{propuesta.tamano_usd:.2f}"),
            razon=propuesta.razon,
        )
        sesion.add(sugerencia)

        sesion.add(
            PosicionSombra(
                symbol=propuesta.symbol,
                fecha_entrada=resultado.fecha,
                precio_entrada=Decimal(f"{propuesta.precio_referencia:.6f}"),
                tamano_usd=Decimal(f"{propuesta.tamano_usd:.2f}"),
                stop=Decimal(f"{propuesta.stop:.6f}"),
                abierta=True,
            )
        )
        guardadas += 1

    await sesion.commit()
    resultado.sugerencias_guardadas = guardadas
    return guardadas


async def ejecutar_digest_diario(fecha: date | None = None) -> ResultadoDigest:
    """Corrida completa: calcula, persiste y envia. La que llama el planificador.

    El kill switch de `/pausar` corta el envio, no el calculo: interesa seguir
    acumulando scores aunque no se quieran recibir mensajes, porque ese
    historial es lo que despues permite evaluar el sistema.
    """
    from investing_bot.db import sesion_bd

    fecha = fecha or date.today()
    async with sesion_bd() as sesion:
        resultado = await generar_digest(sesion, fecha)
        await persistir_digest(sesion, resultado)

        if await estan_pausados(sesion):
            log.warning("digest_no_enviado", motivo="envios pausados con /pausar")
            return resultado

        destino = await chat_destino(sesion)

    if destino is None:
        log.warning("digest_no_enviado", motivo="sin chat vinculado ni configurado")
        return resultado

    config = obtener_config()
    if not config.telegram_bot_token:
        log.warning("digest_no_enviado", motivo="TELEGRAM_BOT_TOKEN vacio")
        return resultado

    from telegram import Bot

    bot = Bot(token=config.telegram_bot_token)
    async with bot:
        mensaje = await bot.send_message(chat_id=destino, text=resultado.texto)

    async with sesion_bd() as sesion:
        await marcar_enviado(
            sesion,
            fecha,
            [p.symbol for p in resultado.decision.propuestas],
            mensaje.message_id,
        )

    resultado.enviado = True
    log.info(
        "digest_enviado",
        fecha=str(fecha),
        sugerencias=len(resultado.decision.propuestas),
        vetos=len(resultado.decision.vetos),
    )
    return resultado


async def marcar_enviado(
    sesion: AsyncSession, fecha: date, symbols: list[str], mensaje_id: int | None
) -> None:
    """Anota en las sugerencias del dia que ya salieron por Telegram."""
    if not symbols:
        return
    await sesion.execute(
        sa.update(Sugerencia)
        .where(
            Sugerencia.symbol.in_(symbols),
            Sugerencia.enviada_at.is_(None),
        )
        .values(enviada_at=ahora_utc(), mensaje_telegram_id=mensaje_id)
    )
    await sesion.commit()
