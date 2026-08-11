"""Gestor de riesgo: el filtro final, y el unico que puede decir que no.

Su trabajo no es encontrar oportunidades, es rechazarlas. Un score alto es
una condicion necesaria para sugerir algo, nunca suficiente: por encima del
motor de senales hay reglas de exposicion, liquidez, concentracion y costo que
mandan sobre cualquier conviccion del modelo.

Con capital pequeno la regla que mas dinero salva no es ninguna de las
sofisticadas, es la del costo de friccion: una comision fija de 0,15 USD sobre
una posicion de 60 USD es un 0,5 % de ida y otro tanto de vuelta. Estrategias
que en el papel se veian rentables mueren ahi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.config import Configuracion, obtener_config
from investing_bot.modelos.posicion_sombra import PosicionSombra
from investing_bot.modelos.sugerencia import Sugerencia
from investing_bot.modelos.ticker import Ticker
from investing_bot.registro import obtener_logger
from investing_bot.senales.motor import ResultadoScore

log = obtener_logger(__name__)

ACCION_COMPRAR = "comprar"
ACCION_VENDER = "vender"
ACCION_MANTENER = "mantener"


@dataclass(slots=True)
class Veto:
    """Una sugerencia rechazada, con el motivo exacto."""

    symbol: str
    regla: str
    motivo: str


@dataclass(slots=True)
class Propuesta:
    """Una sugerencia que paso todos los filtros."""

    symbol: str
    accion: str
    score: float
    precio_referencia: float
    stop: float
    tamano_usd: float
    costo_estimado_usd: float
    costo_pct: float
    razon: str
    resultado: ResultadoScore

    @property
    def distancia_stop_pct(self) -> float:
        return (self.stop - self.precio_referencia) / self.precio_referencia


@dataclass(slots=True)
class DecisionRiesgo:
    """Lo que el gestor deja pasar y lo que corta, con sus motivos."""

    propuestas: list[Propuesta] = field(default_factory=list)
    vetos: list[Veto] = field(default_factory=list)
    modo_defensivo: bool = False
    motivo_defensivo: str = ""
    contexto: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EstadoCartera:
    """Foto de la cartera que necesita el gestor para decidir."""

    posiciones_abiertas: dict[str, str | None] = field(default_factory=dict)
    sugerencias_hoy: int = 0
    ultimo_sugerido: dict[str, date] = field(default_factory=dict)
    perdida_mensual_pct: float = 0.0


class GestorRiesgo:
    """Aplica las reglas del SPEC 6.4 y veta lo que no pasa."""

    def __init__(self, config: Configuracion | None = None) -> None:
        self.config = config or obtener_config()

    # --- Estado de la cartera -------------------------------------------

    async def leer_estado(self, sesion: AsyncSession, fecha: date) -> EstadoCartera:
        """Reconstruye la exposicion actual desde el portafolio sombra."""
        abiertas = (
            await sesion.execute(
                sa.select(PosicionSombra.symbol, Ticker.sector)
                .outerjoin(Ticker, Ticker.symbol == PosicionSombra.symbol)
                .where(PosicionSombra.abierta.is_(True))
            )
        ).all()

        sugerencias_hoy = int(
            await sesion.scalar(
                sa.select(sa.func.count())
                .select_from(Sugerencia)
                .where(
                    Sugerencia.accion == ACCION_COMPRAR, sa.func.date(Sugerencia.creado_at) == fecha
                )
            )
            or 0
        )

        recientes = (
            await sesion.execute(
                sa.select(Sugerencia.symbol, sa.func.max(Sugerencia.creado_at))
                .where(Sugerencia.accion == ACCION_COMPRAR)
                .group_by(Sugerencia.symbol)
            )
        ).all()

        return EstadoCartera(
            posiciones_abiertas={fila.symbol: fila.sector for fila in abiertas},
            sugerencias_hoy=sugerencias_hoy,
            ultimo_sugerido={
                symbol: momento.date() for symbol, momento in recientes if momento is not None
            },
            perdida_mensual_pct=await self._perdida_mensual(sesion, fecha),
        )

    async def _perdida_mensual(self, sesion: AsyncSession, fecha: date) -> float:
        """Perdida realizada del mes en curso, como fraccion del capital."""
        inicio_mes = fecha.replace(day=1)
        cerradas = (
            await sesion.scalars(
                sa.select(PosicionSombra).where(
                    PosicionSombra.abierta.is_(False),
                    PosicionSombra.fecha_salida >= inicio_mes,
                )
            )
        ).all()

        resultado = 0.0
        for posicion in cerradas:
            if posicion.precio_salida is None or not posicion.precio_entrada:
                continue
            retorno = (float(posicion.precio_salida) - float(posicion.precio_entrada)) / float(
                posicion.precio_entrada
            )
            resultado += retorno * float(posicion.tamano_usd)

        capital = float(self.config.capital_total_usd)
        return min(0.0, resultado) / capital if capital else 0.0

    # --- Decision --------------------------------------------------------

    def evaluar(
        self,
        resultados: list[ResultadoScore],
        estado: EstadoCartera,
        tickers: dict[str, Ticker],
    ) -> DecisionRiesgo:
        """Convierte scores en propuestas, o en vetos con su motivo."""
        cfg = self.config
        decision = DecisionRiesgo()

        regimen = resultados[0].regimen if resultados else None
        perdida_excedida = estado.perdida_mensual_pct <= -float(cfg.max_perdida_mensual_pct)

        if regimen is not None and regimen.modo_defensivo:
            decision.modo_defensivo = True
            decision.motivo_defensivo = regimen.resumen
        if perdida_excedida:
            decision.modo_defensivo = True
            motivo = (
                f"perdida mensual {estado.perdida_mensual_pct:.1%} supera el limite "
                f"de {float(cfg.max_perdida_mensual_pct):.0%}"
            )
            decision.motivo_defensivo = (
                f"{decision.motivo_defensivo}; {motivo}" if decision.motivo_defensivo else motivo
            )

        decision.contexto = {
            "posiciones_abiertas": len(estado.posiciones_abiertas),
            "max_posiciones": cfg.max_posiciones_abiertas,
            "perdida_mensual_pct": round(estado.perdida_mensual_pct, 4),
            "capital_usd": float(cfg.capital_total_usd),
        }

        sectores = [s for s in estado.posiciones_abiertas.values() if s]
        emitidas = 0

        for resultado in resultados:
            symbol = resultado.symbol
            ticker = tickers.get(symbol)

            if resultado.score < cfg.umbral_sugerencia:
                continue  # no llega al umbral: no es un veto, simplemente no destaca

            veto = self._vetar(
                resultado, ticker, estado, sectores, decision.modo_defensivo, emitidas
            )
            if veto is not None:
                decision.vetos.append(veto)
                continue

            propuesta = self._construir_propuesta(resultado)
            if propuesta is None:
                decision.vetos.append(self._motivo_sin_propuesta(resultado))
                continue

            costo_maximo = propuesta.tamano_usd * float(cfg.max_costo_friccion_pct)
            if propuesta.costo_estimado_usd > costo_maximo:
                decision.vetos.append(
                    Veto(
                        symbol,
                        "costo_friccion",
                        f"El costo estimado ({propuesta.costo_pct:.2%}) supera el "
                        f"{float(cfg.max_costo_friccion_pct):.0%} del tamano de la posicion",
                    )
                )
                continue

            decision.propuestas.append(propuesta)
            emitidas += 1
            if ticker is not None and ticker.sector:
                sectores.append(ticker.sector)

        return decision

    def _vetar(
        self,
        resultado: ResultadoScore,
        ticker: Ticker | None,
        estado: EstadoCartera,
        sectores: list[str],
        modo_defensivo: bool,
        emitidas: int,
    ) -> Veto | None:
        """Primera regla que falle. El orden va de mas barato a mas caro."""
        cfg = self.config
        symbol = resultado.symbol

        if ticker is None or not ticker.en_whitelist:
            return Veto(symbol, "whitelist", "Fuera de la whitelist")

        if modo_defensivo:
            return Veto(symbol, "modo_defensivo", "Modo defensivo: no se abren compras nuevas")

        if symbol in estado.posiciones_abiertas:
            return Veto(symbol, "posicion_abierta", "Ya hay una posicion abierta en este ticker")

        if len(estado.posiciones_abiertas) >= cfg.max_posiciones_abiertas:
            return Veto(
                symbol,
                "max_posiciones",
                f"Ya hay {len(estado.posiciones_abiertas)} posiciones abiertas "
                f"(maximo {cfg.max_posiciones_abiertas})",
            )

        if emitidas + estado.sugerencias_hoy >= cfg.max_sugerencias_por_dia:
            return Veto(
                symbol,
                "max_sugerencias_dia",
                f"Ya se emitieron {cfg.max_sugerencias_por_dia} sugerencias hoy",
            )

        ultima = estado.ultimo_sugerido.get(symbol)
        if ultima is not None:
            dias = (resultado.fecha - ultima).days
            if dias < cfg.dias_cooldown_mismo_ticker:
                return Veto(
                    symbol,
                    "cooldown",
                    f"Sugerido hace {dias} dias (cooldown de {cfg.dias_cooldown_mismo_ticker})",
                )

        if ticker.sector and sectores.count(ticker.sector) >= cfg.max_posiciones_por_sector:
            return Veto(
                symbol,
                "concentracion_sector",
                f"Ya hay {cfg.max_posiciones_por_sector} posiciones en {ticker.sector}",
            )

        precio = resultado.precio_referencia
        if precio is None or precio < float(cfg.min_precio_accion):
            return Veto(
                symbol,
                "precio_minimo",
                f"Precio {precio} por debajo del minimo de {float(cfg.min_precio_accion)}",
            )

        volumen = float(ticker.volumen_promedio_30d or 0)
        if volumen < cfg.min_volumen_diario:
            return Veto(
                symbol,
                "liquidez",
                f"Volumen medio {volumen:,.0f} por debajo de {cfg.min_volumen_diario:,}",
            )

        if not resultado.hay_alguna_senal:
            return Veto(
                symbol,
                "sin_evidencia",
                "Ninguna senal tuvo datos suficientes: el score no significa nada",
            )

        return None

    def _motivo_sin_propuesta(self, resultado: ResultadoScore) -> Veto:
        """Explica por que no se pudo construir la propuesta."""
        cfg = self.config
        if resultado.precio_referencia is None or resultado.precio_referencia <= 0:
            return Veto(
                resultado.symbol, "precio_referencia", "Sin precio de referencia utilizable"
            )

        capital = float(cfg.capital_total_usd)
        maximo = capital * float(cfg.max_pct_por_posicion)
        return Veto(
            resultado.symbol,
            "tamano_minimo",
            f"El maximo por posicion (${maximo:,.2f} = "
            f"{float(cfg.max_pct_por_posicion):.0%} de ${capital:,.0f}) no llega al minimo "
            f"viable de ${float(cfg.min_tamano_posicion_usd):,.0f}. Con este capital el "
            f"sistema no puede operar sin romper su propio limite de concentracion.",
        )

    def _construir_propuesta(self, resultado: ResultadoScore) -> Propuesta | None:
        """Calcula tamano, stop y costo de una sugerencia de compra."""
        cfg = self.config
        precio = resultado.precio_referencia
        if precio is None or precio <= 0:
            return None

        capital = float(cfg.capital_total_usd)
        tamano = min(capital * float(cfg.max_pct_por_posicion), capital)

        # `min_tamano_posicion_usd` es un filtro de viabilidad, NO un objetivo.
        # Subir la posicion hasta el minimo romperia `max_pct_por_posicion`, que
        # es la regla que limita la concentracion. Si el maximo permitido no
        # llega al minimo viable, la respuesta correcta es no operar.
        if tamano < float(cfg.min_tamano_posicion_usd):
            return None

        stop = precio * (1 - float(cfg.stop_loss_pct))

        # Ida y vuelta: dos comisiones fijas mas el spread estimado en cada
        # extremo. Ignorar el retorno es como mirar solo la mitad del viaje.
        costo = 2 * float(cfg.costo_clearing_usd) + 2 * tamano * float(cfg.spread_estimado_pct)
        costo_pct = costo / tamano if tamano else 1.0

        resumenes = [
            f"{c.nombre} {c.puntos:+.0f} ({c.resumen})"
            for c in resultado.componentes
            if c.datos_suficientes
        ]
        return Propuesta(
            symbol=resultado.symbol,
            accion=ACCION_COMPRAR,
            score=resultado.score,
            precio_referencia=precio,
            stop=round(stop, 2),
            tamano_usd=round(tamano, 2),
            costo_estimado_usd=round(costo, 2),
            costo_pct=costo_pct,
            razon="; ".join(resumenes) or "Sin componentes con datos",
            resultado=resultado,
        )


def a_decimal(valor: float) -> Decimal:
    """Convierte a Decimal con la precision de las columnas monetarias."""
    return Decimal(f"{valor:.6f}")
