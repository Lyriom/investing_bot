"""Construccion de los mensajes que envia el bot.

Funciones puras (datos -> texto) para que se puedan probar sin red ni bot.
Se emiten en texto plano a proposito: ni Markdown ni HTML, para que un nombre
de empresa con un guion bajo o un asterisco no rompa el mensaje.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
    from zoneinfo import ZoneInfo

    local = momento.astimezone(ZoneInfo(zona_operador))
    return f"{local.day} {MESES[local.month - 1]} {local:%H:%M}"


def formatear_bienvenida(chat_id: int) -> str:
    """Respuesta a /start."""
    return (
        "investing_bot vinculado.\n"
        f"chat_id: {chat_id}\n\n"
        "Este sistema NO ejecuta ordenes. Sugiere, explica y registra; "
        "la decision y la ejecucion son tuyas.\n\n"
        "Disponible ahora (FASE 0):\n"
        "  /estado  - estado del pipeline y de los datos\n\n"
        "Llegan en fases posteriores: /hoy, /desglose, /registrar, /pausar."
    )


def formatear_estado(
    *,
    entorno: str,
    capital_usd: Decimal,
    conteos: dict[str, int],
    ingestores: list[EstadoIngestor],
    zona_operador: str,
) -> str:
    """Respuesta a /estado.

    En FASE 0 no hay portafolio ni regimen de mercado, y el mensaje lo dice en
    vez de mostrar ceros que se leerian como informacion real.
    """
    lineas = [
        "Estado del sistema",
        f"Entorno: {entorno}   Capital configurado: USD {capital_usd}",
        "",
        "Datos",
        f"  Tickers en whitelist : {conteos.get('tickers_whitelist', 0)}",
        f"  Barras de precio     : {conteos.get('barras_precio', 0)}",
        f"  Ejecuciones registradas: {conteos.get('ejecuciones', 0)}",
        "",
        "Ingestores",
    ]

    for ingestor in ingestores:
        if not ingestor.implementado:
            lineas.append(f"  {ingestor.nombre:<9} pendiente (FASE 1)")
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

    lineas += [
        "",
        "Portafolio y regimen de mercado: no disponibles en FASE 0.",
        "Las senales llegan en FASE 3, despues de validar el backtester.",
    ]
    return "\n".join(lineas)
