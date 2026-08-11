"""Las cuatro senales del SPEC 7 y el motor que las combina."""

from investing_bot.senales.base import ComponenteSenal, Senal
from investing_bot.senales.consenso_congreso import ConsensoCongreso
from investing_bot.senales.deriva_noticias import DerivaNoticias
from investing_bot.senales.motor import MotorSenales, ResultadoScore
from investing_bot.senales.regimen import (
    REGIMEN_ALCISTA,
    REGIMEN_DESCONOCIDO,
    REGIMEN_RIESGO,
    EstadoRegimen,
    evaluar_regimen,
)
from investing_bot.senales.velocidad_reddit import VelocidadReddit

__all__ = [
    "REGIMEN_ALCISTA",
    "REGIMEN_DESCONOCIDO",
    "REGIMEN_RIESGO",
    "ComponenteSenal",
    "ConsensoCongreso",
    "DerivaNoticias",
    "EstadoRegimen",
    "MotorSenales",
    "ResultadoScore",
    "Senal",
    "VelocidadReddit",
    "evaluar_regimen",
]
