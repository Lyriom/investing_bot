"""Normalizacion: texto libre -> ticker, y deduplicacion de noticias."""

from investing_bot.normalizador.deduplicador import (
    buscar_original,
    hash_titular,
    son_casi_iguales,
)
from investing_bot.normalizador.entidades import ResolutorEntidades, construir_resolutor

__all__ = [
    "ResolutorEntidades",
    "buscar_original",
    "construir_resolutor",
    "hash_titular",
    "son_casi_iguales",
]
