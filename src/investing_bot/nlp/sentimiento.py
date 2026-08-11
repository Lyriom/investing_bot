"""Clasificacion de sentimiento financiero.

Modelo principal: FinBERT (`ProsusAI/finbert`), entrenado sobre texto
financiero. Se carga de forma perezosa porque tarda y pesa.

Respaldo: un lexico financiero pequeno, para que el pipeline funcione en
servidores donde instalar torch (~2 GB) no es razonable. El respaldo es
claramente peor, y por eso **cada fila guarda en `modelo_usado` con que se
clasifico**: nunca se puede confundir una salida de FinBERT con una del
lexico al analizar resultados despues.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

MODELO_FINBERT = "ProsusAI/finbert"
ETIQUETA_FINBERT = "finbert-v1"
ETIQUETA_LEXICO = "lexico-v1"

# Lexico financiero minimo del respaldo. No pretende competir con FinBERT:
# pretende que el sistema no se quede sin ninguna senal de sentimiento.
POSITIVAS: frozenset[str] = frozenset(
    {
        "beat",
        "beats",
        "surge",
        "surges",
        "soar",
        "soars",
        "rally",
        "rallies",
        "jump",
        "jumps",
        "gain",
        "gains",
        "record",
        "upgrade",
        "upgraded",
        "outperform",
        "strong",
        "growth",
        "profit",
        "profits",
        "raise",
        "raises",
        "raised",
        "exceed",
        "exceeds",
        "bullish",
        "approval",
        "approved",
        "win",
        "wins",
        "expansion",
        "optimistic",
        "rebound",
        "recovery",
    }
)
NEGATIVAS: frozenset[str] = frozenset(
    {
        "miss",
        "misses",
        "missed",
        "plunge",
        "plunges",
        "slump",
        "slumps",
        "fall",
        "falls",
        "drop",
        "drops",
        "decline",
        "declines",
        "downgrade",
        "downgraded",
        "underperform",
        "weak",
        "loss",
        "losses",
        "cut",
        "cuts",
        "warning",
        "warns",
        "bearish",
        "lawsuit",
        "probe",
        "investigation",
        "recall",
        "layoff",
        "layoffs",
        "bankruptcy",
        "fraud",
        "delay",
        "delays",
        "halt",
        "halted",
        "concern",
        "concerns",
        "risk",
        "risks",
    }
)


@dataclass(frozen=True, slots=True)
class Sentimiento:
    """Resultado de clasificar un texto."""

    valor: float  # [-1, 1]
    confianza: float  # [0, 1]
    modelo: str


NEUTRO = Sentimiento(valor=0.0, confianza=0.0, modelo=ETIQUETA_LEXICO)


def _clasificar_con_lexico(texto: str) -> Sentimiento:
    """Cuenta palabras del lexico y normaliza por el total de coincidencias."""
    from investing_bot.normalizador.entidades import normalizar_texto

    tokens = normalizar_texto(texto).split()
    positivas = sum(1 for t in tokens if t in POSITIVAS)
    negativas = sum(1 for t in tokens if t in NEGATIVAS)
    total = positivas + negativas
    if total == 0:
        return NEUTRO

    valor = (positivas - negativas) / total
    # La confianza crece con la cantidad de evidencia, pero se topa bajo:
    # un lexico de 60 palabras no merece mas que eso.
    confianza = min(0.6, 0.2 + 0.1 * total)
    return Sentimiento(valor=valor, confianza=confianza, modelo=ETIQUETA_LEXICO)


class ClasificadorSentimiento:
    """Fachada sobre FinBERT con respaldo lexico.

    Es un singleton perezoso: cargar FinBERT cuesta segundos y cientos de MB,
    asi que se hace una sola vez y solo si de verdad se va a usar.
    """

    _instancia: ClasificadorSentimiento | None = None
    _candado = threading.Lock()

    def __init__(self, forzar_lexico: bool = False) -> None:
        self._forzar_lexico = forzar_lexico
        self._pipeline: Callable[[list[str]], list[dict[str, object]]] | None = None
        self._finbert_disponible: bool | None = None

    @classmethod
    def instancia(cls) -> ClasificadorSentimiento:
        """Devuelve el clasificador compartido del proceso."""
        with cls._candado:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    @property
    def modelo_activo(self) -> str:
        """Etiqueta del modelo que se usaria ahora mismo."""
        return ETIQUETA_LEXICO if self._forzar_lexico or not self._cargar() else ETIQUETA_FINBERT

    def _cargar(self) -> bool:
        """Intenta cargar FinBERT una sola vez. Devuelve si esta disponible."""
        if self._forzar_lexico:
            return False
        if self._finbert_disponible is not None:
            return self._finbert_disponible

        try:
            from transformers import pipeline

            self._pipeline = pipeline(
                "sentiment-analysis",
                model=MODELO_FINBERT,
                truncation=True,
                max_length=512,
            )
            self._finbert_disponible = True
            log.info("finbert_cargado", modelo=MODELO_FINBERT)
        except Exception as exc:  # noqa: BLE001 - la ausencia de torch es esperada
            self._finbert_disponible = False
            log.warning(
                "finbert_no_disponible",
                motivo=str(exc),
                accion="Se clasifica con el lexico. Instalar el extra `nlp` para usar FinBERT.",
            )
        return self._finbert_disponible

    def clasificar(self, texto: str) -> Sentimiento:
        """Clasifica un texto. Nunca lanza: ante la duda devuelve neutro."""
        return self.clasificar_lote([texto])[0]

    def clasificar_lote(self, textos: Sequence[str]) -> list[Sentimiento]:
        """Clasifica varios textos de una vez.

        FinBERT es mucho mas rapido por lote que texto a texto, y las noticias
        siempre llegan en tandas.
        """
        if not textos:
            return []

        if not self._cargar():
            return [_clasificar_con_lexico(t) for t in textos]

        if self._pipeline is None:  # pragma: no cover - _cargar ya lo garantiza
            return [_clasificar_con_lexico(t) for t in textos]

        try:
            salidas = self._pipeline(list(textos))
        except Exception:  # noqa: BLE001 - si FinBERT falla, no se pierde la ingesta
            log.exception("finbert_fallo_al_clasificar")
            return [_clasificar_con_lexico(t) for t in textos]

        resultados = []
        for salida in salidas:
            etiqueta = str(salida.get("label", "")).lower()
            puntaje = float(salida.get("score", 0.0))  # type: ignore[arg-type]
            if etiqueta == "positive":
                valor = puntaje
            elif etiqueta == "negative":
                valor = -puntaje
            else:  # neutral
                valor = 0.0
            resultados.append(Sentimiento(valor=valor, confianza=puntaje, modelo=ETIQUETA_FINBERT))
        return resultados


def clasificar(texto: str) -> Sentimiento:
    """Atajo sobre el clasificador compartido."""
    return ClasificadorSentimiento.instancia().clasificar(texto)
