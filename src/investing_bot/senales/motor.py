"""Motor de combinacion: de las cuatro senales a un score de 0 a 100.

Esquema de puntuacion (SPEC 7): suma ponderada de S1-S3, con S4 aplicado como
multiplicador. Normalizado a 0-100, donde **50 es neutro**, no cero: un ticker
sin ninguna evidencia a favor ni en contra debe quedar en el medio, no en el
suelo.

Los pesos son PROVISIONALES, fijos y arbitrarios (invariante I4). No se tocan
hasta que el backtester de la FASE 2 diga algo. Se guardan en cada fila junto
al score, para que dentro de seis meses se sepa con que version se genero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.config import obtener_config
from investing_bot.datos.repositorio_pit import RepositorioPIT
from investing_bot.modelos.senal import Senal as SenalModelo
from investing_bot.modelos.ticker import Ticker
from investing_bot.registro import obtener_logger
from investing_bot.senales.base import ComponenteSenal
from investing_bot.senales.consenso_congreso import ConsensoCongreso
from investing_bot.senales.deriva_noticias import DerivaNoticias
from investing_bot.senales.regimen import EstadoRegimen, evaluar_regimen
from investing_bot.senales.velocidad_reddit import VelocidadReddit

log = obtener_logger(__name__)

SCORE_NEUTRO = 50.0


@dataclass(slots=True)
class ResultadoScore:
    """Score de un ticker con todo lo necesario para auditarlo."""

    symbol: str
    fecha: date
    score: float
    componentes: list[ComponenteSenal]
    regimen: EstadoRegimen
    version_modelo: str
    score_antes_regimen: float
    precio_referencia: float | None = None
    detalle_extra: dict[str, Any] = field(default_factory=dict)

    @property
    def hay_alguna_senal(self) -> bool:
        """Si ninguna senal tuvo datos, el score es 50 por defecto, no informacion."""
        return any(c.datos_suficientes for c in self.componentes)

    def componentes_dict(self) -> dict[str, Any]:
        """Lo que se guarda en `senales.componentes` (invariante I3)."""
        datos: dict[str, Any] = {c.nombre: c.a_dict() for c in self.componentes}
        datos["regimen"] = {
            "estado": self.regimen.regimen,
            "modo_defensivo": self.regimen.modo_defensivo,
            "puntos": round(self.score - self.score_antes_regimen, 2),
            "resumen": self.regimen.resumen,
            "detalle": self.regimen.detalle,
        }
        return datos


class MotorSenales:
    """Combina las senales y persiste el resultado."""

    def __init__(self) -> None:
        config = obtener_config()
        self.version_modelo = config.version_modelo
        self.multiplicador_riesgo = config.multiplicador_regimen_riesgo
        self.senales = [
            DerivaNoticias(config.peso_deriva_noticias),
            VelocidadReddit(config.peso_velocidad_reddit),
            ConsensoCongreso(config.peso_consenso_congreso),
        ]
        self.peso_total = sum(s.peso for s in self.senales)

    async def calcular(
        self,
        repositorio: RepositorioPIT,
        symbol: str,
        fecha: date,
        regimen: EstadoRegimen | None = None,
    ) -> ResultadoScore:
        """Score de un ticker en una fecha."""
        if regimen is None:
            regimen = await evaluar_regimen(repositorio, fecha)

        componentes = [await senal.calcular(repositorio, symbol, fecha) for senal in self.senales]

        # Se divide por el peso total, no por el de las senales con datos: una
        # senal sin datos debe empujar hacia lo neutro, no dejar que las otras
        # se repartan su peso y exageren su conviccion.
        bruto = sum(c.valor * c.peso for c in componentes if c.datos_suficientes)
        normalizado = bruto / self.peso_total if self.peso_total else 0.0
        score_antes = SCORE_NEUTRO + normalizado * SCORE_NEUTRO

        score = score_antes
        if regimen.modo_defensivo and score > SCORE_NEUTRO:
            # El regimen no puede subir un score, solo recortarlo.
            score = SCORE_NEUTRO + (score - SCORE_NEUTRO) * self.multiplicador_riesgo

        barra = await repositorio.ultimo_cierre(symbol)

        return ResultadoScore(
            symbol=symbol,
            fecha=fecha,
            score=round(max(0.0, min(100.0, score)), 2),
            componentes=componentes,
            regimen=regimen,
            version_modelo=self.version_modelo,
            score_antes_regimen=round(score_antes, 2),
            precio_referencia=float(barra.cierre) if barra is not None else None,
        )

    async def calcular_whitelist(self, sesion: AsyncSession, fecha: date) -> list[ResultadoScore]:
        """Score de todos los tickers de la whitelist, de mayor a menor."""
        repositorio = RepositorioPIT(sesion, fecha)
        symbols = list(
            (
                await sesion.scalars(
                    sa.select(Ticker.symbol)
                    .where(Ticker.en_whitelist.is_(True), Ticker.activo.is_(True))
                    .order_by(Ticker.symbol)
                )
            ).all()
        )
        if not symbols:
            return []

        regimen = await evaluar_regimen(repositorio, fecha)
        resultados = []
        for symbol in symbols:
            try:
                resultados.append(await self.calcular(repositorio, symbol, fecha, regimen))
            except Exception:  # noqa: BLE001 - un ticker roto no tumba el digest
                log.exception("score_fallo", symbol=symbol, fecha=str(fecha))

        return sorted(resultados, key=lambda r: r.score, reverse=True)

    async def persistir(self, sesion: AsyncSession, resultados: list[ResultadoScore]) -> int:
        """Guarda los scores. Idempotente por (symbol, fecha, version_modelo)."""
        guardados = 0
        for resultado in resultados:
            existente = await sesion.scalar(
                sa.select(SenalModelo).where(
                    SenalModelo.symbol == resultado.symbol,
                    SenalModelo.fecha == resultado.fecha,
                    SenalModelo.version_modelo == resultado.version_modelo,
                )
            )
            componentes = resultado.componentes_dict()
            score = Decimal(f"{resultado.score:.4f}")

            if existente is None:
                sesion.add(
                    SenalModelo(
                        symbol=resultado.symbol,
                        fecha=resultado.fecha,
                        score_total=score,
                        componentes=componentes,
                        regimen_mercado=resultado.regimen.regimen,
                        version_modelo=resultado.version_modelo,
                    )
                )
                guardados += 1
            else:
                existente.score_total = score
                existente.componentes = componentes
                existente.regimen_mercado = resultado.regimen.regimen

        await sesion.commit()
        return guardados
