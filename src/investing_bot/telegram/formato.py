"""Construccion de los mensajes que envia el bot.

Funciones puras (datos -> texto) para que se puedan probar sin red ni bot.
Se emiten en texto plano a proposito: ni Markdown ni HTML, para que un nombre
de empresa con un guion bajo o un asterisco no rompa el mensaje.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from investing_bot.servicios.consultas import EstadoIngestor

MESES = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)


def formatear_fecha_corta(momento: datetime | None, zona_operador: str) -> str:
    """Fecha legible en la zona horaria del operador."""
    if momento is None:
        return "nunca"
    local = momento.astimezone(ZoneInfo(zona_operador))
    return f"{local.day} {MESES[local.month - 1]} {local:%H:%M}"


def formatear_bienvenida(chat_id: int) -> str:
    """Respuesta a /start."""
    return (
        "investing_bot vinculado.\n"
        f"chat_id: {chat_id}\n\n"
        "Este sistema NO ejecuta ordenes. Sugiere, explica y registra; "
        "la decision y la ejecucion son tuyas.\n\n"
        "Comandos:\n"
        "  /estado     - pipeline, datos y posiciones\n"
        "  /hoy        - digest del dia\n"
        "  /desglose X - componentes del score de un ticker\n"
        "  /registrar  - grabar una operacion real\n"
        "  /pausar /reanudar - kill switch de los envios"
    )


def formatear_estado(
    *,
    entorno: str,
    capital_usd: Decimal,
    conteos: dict[str, int],
    ingestores: list[EstadoIngestor],
    zona_operador: str,
    pausado: bool = False,
    posiciones: list[tuple[str, float, float | None]] | None = None,
) -> str:
    """Respuesta a /estado."""
    lineas = [
        "Estado del sistema",
        f"Entorno: {entorno}   Capital configurado: USD {capital_usd}",
    ]
    if pausado:
        lineas.append("ENVIOS PAUSADOS (/reanudar para volver a activarlos)")

    lineas += [
        "",
        "Datos",
        f"  Tickers en whitelist   : {conteos.get('tickers_whitelist', 0)}",
        f"  Barras de precio       : {conteos.get('barras_precio', 0)}",
        f"  Noticias               : {conteos.get('noticias', 0)}",
        f"  Dias-ticker de Reddit  : {conteos.get('reddit', 0)}",
        f"  Trades del Congreso    : {conteos.get('congreso', 0)}",
        f"  Ejecuciones registradas: {conteos.get('ejecuciones', 0)}",
        "",
        "Ingestores",
    ]

    for ingestor in ingestores:
        if not ingestor.implementado:
            lineas.append(f"  {ingestor.nombre:<9} pendiente")
            continue
        if ingestor.ultima_corrida is None:
            lineas.append(f"  {ingestor.nombre:<9} sin corridas todavia")
            continue
        marca = "ok" if ingestor.exito else "FALLO"
        cuando = formatear_fecha_corta(ingestor.ultima_corrida, zona_operador)
        lineas.append(
            f"  {ingestor.nombre:<9} {marca}  {cuando}  "
            f"+{ingestor.filas_nuevas} nuevas / {ingestor.filas_actualizadas} act."
        )
        if ingestor.errores:
            for error in ingestor.errores[:2]:
                lineas.append(f"      ! {error}")

    lineas.append("")
    lineas.append("Portafolio sombra (sin dinero real)")
    if posiciones:
        for symbol, entrada, stop in posiciones:
            texto_stop = f"stop ${stop:,.2f}" if stop is not None else "sin stop"
            lineas.append(f"  {symbol:<6} entrada ${entrada:,.2f}   {texto_stop}")
    else:
        lineas.append("  sin posiciones abiertas")

    return "\n".join(lineas)
