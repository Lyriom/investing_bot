"""S1 — Deriva post-noticia. Peso 0.40. Horizonte 3-15 dias.

Anomalia documentada (post-earnings announcement drift): tras una sorpresa
fuerte, el precio tiende a seguir derivando en la direccion de la sorpresa
durante dias. Es la unica de las cuatro senales con respaldo academico solido.

Dos decisiones que definen esta senal:

1. **Se activa en los dias posteriores, nunca en el minuto del titular.**
   Competir por velocidad contra firmas colocadas en el datacenter del NASDAQ,
   desde una conexion domestica, no es una estrategia. Las noticias de menos de
   `HORAS_MINIMAS` de antiguedad se ignoran a proposito.

2. **El precio tiene que confirmar.** Un titular positivo cuyo precio no se
   movio no es deriva: es ruido con buen vocabulario.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import ClassVar

from investing_bot.datos.repositorio_pit import RepositorioPIT, fin_del_dia
from investing_bot.senales.base import ComponenteSenal, Senal, sin_datos

VENTANA_DIAS = 15
# Por debajo de esto la noticia es demasiado reciente: es el terreno de los
# sistemas de baja latencia, no el nuestro.
HORAS_MINIMAS = 24
# Vida media del peso por recencia, en dias.
VIDA_MEDIA_DIAS = 7.0
CONFIANZA_MINIMA = 0.35
# Si el precio contradice al titular, el aporte se recorta a esta fraccion.
FACTOR_SIN_CONFIRMACION = 0.4


class DerivaNoticias(Senal):
    """Sentimiento agregado de las noticias recientes, confirmado por el precio."""

    nombre: ClassVar[str] = "deriva"

    async def calcular(
        self, repositorio: RepositorioPIT, symbol: str, fecha: date
    ) -> ComponenteSenal:
        noticias = await repositorio.noticias(symbol, dias=VENTANA_DIAS)
        corte_reciente = fin_del_dia(fecha) - timedelta(hours=HORAS_MINIMAS)

        utiles = [
            n
            for n in noticias
            if n.sentimiento is not None
            and n.confianza is not None
            and float(n.confianza) >= CONFIANZA_MINIMA
            and n.event_at.replace(tzinfo=n.event_at.tzinfo or corte_reciente.tzinfo)
            <= corte_reciente
        ]
        if not utiles:
            return sin_datos(
                self.nombre, self.peso, f"Sin noticias clasificadas en {VENTANA_DIAS} dias"
            )

        numerador = 0.0
        denominador = 0.0
        for noticia in utiles:
            dias = max(0.0, (fecha - noticia.event_at.date()).days)
            peso_recencia = math.exp(-dias / VIDA_MEDIA_DIAS)
            peso = float(noticia.confianza) * peso_recencia  # type: ignore[arg-type]
            numerador += float(noticia.sentimiento) * peso  # type: ignore[arg-type]
            denominador += peso

        if denominador == 0:
            return sin_datos(self.nombre, self.peso, "Noticias demasiado antiguas")

        sentimiento = numerador / denominador

        # Confirmacion por precio: retorno desde la noticia mas influyente.
        mas_influyente = max(utiles, key=lambda n: abs(float(n.sentimiento or 0)))
        retorno = await self._retorno_desde(repositorio, symbol, mas_influyente.event_at.date())

        confirmado = retorno is not None and (retorno * sentimiento) > 0
        factor = 1.0 if confirmado else FACTOR_SIN_CONFIRMACION
        valor = max(-1.0, min(1.0, sentimiento * factor))

        modelos = {n.modelo_usado for n in utiles if n.modelo_usado}
        return ComponenteSenal(
            nombre=self.nombre,
            valor=valor,
            peso=self.peso,
            datos_suficientes=True,
            resumen=(
                f"sent {sentimiento:+.2f} en {len(utiles)} noticias"
                + (f", precio {retorno:+.1%} confirma" if confirmado else ", precio no confirma")
            ),
            detalle={
                "noticias": len(utiles),
                "sentimiento_agregado": round(sentimiento, 4),
                "retorno_desde_evento": round(retorno, 4) if retorno is not None else None,
                "confirmado_por_precio": confirmado,
                "dias_desde_evento": (fecha - mas_influyente.event_at.date()).days,
                "modelos": sorted(modelos),
            },
        )

    async def _retorno_desde(
        self, repositorio: RepositorioPIT, symbol: str, desde: date
    ) -> float | None:
        """Retorno acumulado del cierre desde la fecha del evento hasta el corte."""
        barras = await repositorio.precios(symbol, dias=VENTANA_DIAS + 10)
        if len(barras) < 2:
            return None

        posteriores = [b for b in barras if b.fecha >= desde]
        if len(posteriores) < 2:
            return None

        cierre_final = float(posteriores[0].cierre)
        cierre_inicial = float(posteriores[-1].cierre)
        if cierre_inicial == 0:
            return None
        return (cierre_final - cierre_inicial) / cierre_inicial
