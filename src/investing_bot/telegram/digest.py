"""Construccion del digest diario.

Formato del SPEC 6.5. Cada sugerencia lleva su desglose numerico, su precio de
invalidacion y su tamano: sin las tres cosas no se emite (invariante I3).

Lleva ademas una advertencia fija sobre que las senales no pasaron por el
backtester. Se quita cuando la FASE 2 exista y diga que esto tiene alguna
ventaja sobre comprar el indice y quedarse quieto.
"""

from __future__ import annotations

from datetime import date

from investing_bot.riesgo.gestor import DecisionRiesgo, Propuesta
from investing_bot.senales.motor import ResultadoScore
from investing_bot.telegram.formato import MESES

ADVERTENCIA_SIN_BACKTEST = (
    "⚠ Estas senales NO pasaron por el backtester (FASE 2 pendiente). "
    "Nadie ha comprobado todavia que superen a comprar SPY y quedarse quieto. "
    "Portafolio sombra, no dinero real."
)


def _fecha_larga(fecha: date) -> str:
    return f"{fecha.day} {MESES[fecha.month - 1]} {fecha.year}"


def formatear_propuesta(propuesta: Propuesta) -> str:
    """Una sugerencia con su desglose completo."""
    resultado = propuesta.resultado
    lineas = [f"⚠ Vigilar — {propuesta.symbol} · score {propuesta.score:.0f}/100"]

    componentes = list(resultado.componentes)
    for indice, componente in enumerate(componentes):
        rama = "└" if indice == len(componentes) - 1 and not resultado.regimen.hay_datos else "├"
        etiqueta = {
            "deriva": "Deriva post-noticia",
            "reddit": "Velocidad Reddit",
            "congreso": "Consenso Congreso",
        }.get(componente.nombre, componente.nombre)
        if componente.datos_suficientes:
            lineas.append(
                f"   {rama} {etiqueta:<22}{componente.puntos:+6.0f}   {componente.resumen}"
            )
        else:
            lineas.append(f"   {rama} {etiqueta:<22}     .   {componente.resumen}")

    puntos_regimen = resultado.score - resultado.score_antes_regimen
    lineas.append(f"   └ {'Regimen':<22}{puntos_regimen:+6.0f}   {resultado.regimen.resumen}")

    lineas += [
        "",
        f"   Entrada ref  ${propuesta.precio_referencia:,.2f}",
        f"   Stop         ${propuesta.stop:,.2f}  ({propuesta.distancia_stop_pct:+.1%})",
        f"   Tamano       ${propuesta.tamano_usd:,.0f}",
        f"   Costo est.   ${propuesta.costo_estimado_usd:,.2f}    ({propuesta.costo_pct:.2%})",
        "",
        f"   /desglose {propuesta.symbol}   ·   /registrar",
    ]
    return "\n".join(lineas)


def formatear_digest(
    fecha: date,
    decision: DecisionRiesgo,
    mejores: list[ResultadoScore],
    umbral: int,
) -> str:
    """El mensaje diario completo."""
    regimen = mejores[0].regimen if mejores else None
    encabezado = f"\U0001f4ca {_fecha_larga(fecha)}"
    if regimen is not None:
        encabezado += f" — Regimen: {regimen.regimen.upper()} ({regimen.resumen})"

    partes = [encabezado]

    if decision.modo_defensivo:
        partes.append(f"Modo defensivo: sin compras nuevas.\nMotivo: {decision.motivo_defensivo}")

    partes.append("")

    if decision.propuestas:
        partes.extend(formatear_propuesta(p) + "\n" for p in decision.propuestas)
    else:
        partes.append(_sin_sugerencias(mejores, decision, umbral))

    partes.append(ADVERTENCIA_SIN_BACKTEST)
    return "\n".join(partes).strip()


def _sin_sugerencias(mejores: list[ResultadoScore], decision: DecisionRiesgo, umbral: int) -> str:
    """Explica por que hoy no hay nada. Un digest vacio no dice nada util."""
    lineas = ["Sin sugerencias hoy."]

    if decision.vetos:
        lineas.append("")
        lineas.append("Candidatos vetados por riesgo:")
        for veto in decision.vetos[:5]:
            lineas.append(f"  · {veto.symbol}: {veto.motivo}")

    con_datos = [r for r in mejores if r.hay_alguna_senal]
    if con_datos:
        lineas.append("")
        lineas.append(f"Mejores scores (umbral {umbral}):")
        for resultado in con_datos[:3]:
            lineas.append(f"  · {resultado.symbol}  {resultado.score:.0f}/100")
    else:
        lineas.append("")
        lineas.append(
            "Ninguna senal tuvo datos suficientes. Revisa que los ingestores de "
            "noticias, Reddit y Congreso esten trayendo filas."
        )

    return "\n".join(lineas)


def formatear_desglose(resultado: ResultadoScore) -> str:
    """Respuesta a /desglose: todo el detalle numerico de un ticker."""
    lineas = [
        f"{resultado.symbol} · {resultado.fecha} · score {resultado.score:.1f}/100",
        f"Version del modelo: {resultado.version_modelo}",
        "",
    ]

    for componente in resultado.componentes:
        estado = "" if componente.datos_suficientes else "  (sin datos)"
        lineas.append(f"{componente.nombre.upper()}{estado}")
        lineas.append(f"  puntos     {componente.puntos:+.2f}  (peso {componente.peso})")
        lineas.append(f"  resumen    {componente.resumen}")
        for clave, valor in componente.detalle.items():
            lineas.append(f"  {clave:<12} {valor}")
        lineas.append("")

    lineas.append("REGIMEN")
    lineas.append(f"  estado     {resultado.regimen.regimen}")
    lineas.append(f"  resumen    {resultado.regimen.resumen}")
    lineas.append(
        f"  ajuste     {resultado.score - resultado.score_antes_regimen:+.2f} "
        f"(score antes: {resultado.score_antes_regimen:.1f})"
    )

    if resultado.precio_referencia is not None:
        lineas.append("")
        lineas.append(f"Ultimo cierre conocido: ${resultado.precio_referencia:,.2f}")

    return "\n".join(lineas)
