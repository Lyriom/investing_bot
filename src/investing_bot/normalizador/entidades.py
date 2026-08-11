"""Resolucion de entidades: texto libre -> ticker.

Regla que gobierna todo este modulo (SPEC 6.2): **preferir falsos negativos a
falsos positivos**. Perder una noticia cuesta una observacion; atribuirla al
ticker equivocado envenena la senal de ese ticker y no se nota nunca.

La cascada, de mas a menos fiable:

  1. Cashtag explicito  `$NVDA`      -> intencion inequivoca del autor
  2. Alias curado       "coca cola"  -> diccionario mantenido a mano
  3. Nombre de empresa  "Microsoft"  -> nombre normalizado de `tickers.nombre`
  4. Simbolo suelto     `NVDA`       -> solo si no es ambiguo

Todo lo que no cae en esos cuatro casos se descarta.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

# Simbolos que jamas se aceptan sueltos (sin `$`). Son palabras corrientes en
# ingles, jerga de foro o siglas: "MA" es Massachusetts y media movil, "IT" es
# un pronombre, "ALL" y "SO" son palabras, "T" y "V" son letras.
SIMBOLOS_AMBIGUOS: frozenset[str] = frozenset(
    {
        "A",
        "I",
        "T",
        "V",
        "MA",
        "HD",
        "SO",
        "IT",
        "ON",
        "BE",
        "GO",
        "AT",
        "AM",
        "PM",
        "ALL",
        "ANY",
        "CAN",
        "FOR",
        "NOW",
        "OUT",
        "NEW",
        "ONE",
        "TWO",
        "BIG",
        "EPS",
        "CEO",
        "CFO",
        "IPO",
        "ETF",
        "GDP",
        "CPI",
        "FED",
        "SEC",
        "IRS",
        "USA",
        "USD",
        "EV",
        "AI",
        "DD",
        "TV",
        "PT",
        "OP",
        "GG",
        "US",
        "UK",
        "EU",
        "ATH",
        "IMO",
        "LOL",
        "WSB",
        "EOD",
        "FYI",
        "NFT",
        "YOLO",
        "TLDR",
    }
)

# Nombres de empresa que coinciden con palabras corrientes. Solo se resuelven
# por cashtag o alias, nunca por aparecer sueltos en el texto: "meta" y "visa"
# aparecen a diario en titulares que no hablan de esas empresas.
NOMBRES_AMBIGUOS: frozenset[str] = frozenset({"meta", "visa", "square", "block"})

# Longitud minima de un nombre de empresa para buscarlo en texto libre.
LARGO_MINIMO_NOMBRE = 5

# Alias curados a mano. Es la pieza que mas valor aporta y la que hay que
# mantener cuando se amplie la whitelist.
ALIAS: Mapping[str, str] = {
    "s&p 500": "SPY",
    "sp500": "SPY",
    "nasdaq 100": "QQQ",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "broadcom": "AVGO",
    "intel": "INTC",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "facebook": "META",
    "meta platforms": "META",
    "netflix": "NFLX",
    "disney": "DIS",
    "walt disney": "DIS",
    "tesla": "TSLA",
    "salesforce": "CRM",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "bank of america": "BAC",
    "mastercard": "MA",
    "visa inc": "V",
    "exxon": "XOM",
    "exxon mobil": "XOM",
    "chevron": "CVX",
    "johnson & johnson": "JNJ",
    "j&j": "JNJ",
    "unitedhealth": "UNH",
    "united health": "UNH",
    "pfizer": "PFE",
    "procter & gamble": "PG",
    "procter and gamble": "PG",
    "coca cola": "KO",
    "coca-cola": "KO",
    "walmart": "WMT",
    "costco": "COST",
    "home depot": "HD",
    "at&t": "T",
}

_SUFIJOS_EMPRESA = (
    "incorporated",
    "inc",
    "corporation",
    "corp",
    "company",
    "co",
    "limited",
    "ltd",
    "plc",
    "holdings",
    "holding",
    "group",
    "trust",
    "the",
    "sa",
    "nv",
)

_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z.\-]{0,11})\b")
_SIMBOLO_SUELTO = re.compile(r"\b([A-Z]{2,5})\b")
_NO_ALFANUMERICO = re.compile(r"[^a-z0-9&\s]")
_ESPACIOS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Coincidencia:
    """Un ticker detectado en un texto, con como se detecto y cuanto se confia."""

    symbol: str
    metodo: str
    confianza: float
    fragmento: str


def normalizar_texto(texto: str) -> str:
    """Minusculas, sin tildes, sin puntuacion, espacios colapsados."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    limpio = _NO_ALFANUMERICO.sub(" ", sin_tildes.lower())
    return _ESPACIOS.sub(" ", limpio).strip()


def normalizar_nombre_empresa(nombre: str) -> str:
    """Reduce 'Apple Inc.' a 'apple' para poder buscarlo en texto libre."""
    palabras = normalizar_texto(nombre).split()
    while palabras and palabras[-1] in _SUFIJOS_EMPRESA:
        palabras.pop()
    while palabras and palabras[0] in _SUFIJOS_EMPRESA:
        palabras.pop(0)
    return " ".join(palabras)


class ResolutorEntidades:
    """Resuelve tickers dentro de un texto contra un catalogo concreto."""

    def __init__(self, catalogo: Mapping[str, str | None]) -> None:
        """`catalogo` mapea symbol -> nombre de la empresa (o None)."""
        self.symbols = {s.upper() for s in catalogo}

        self._por_nombre: dict[str, str] = {}
        for symbol, nombre in catalogo.items():
            if not nombre:
                continue
            normalizado = normalizar_nombre_empresa(nombre)
            if len(normalizado) >= LARGO_MINIMO_NOMBRE and normalizado not in NOMBRES_AMBIGUOS:
                self._por_nombre[normalizado] = symbol.upper()

        self._alias = {
            alias: symbol
            for alias, symbol in ALIAS.items()
            if symbol.upper() in self.symbols and alias not in NOMBRES_AMBIGUOS
        }

    def resolver(self, texto: str) -> list[Coincidencia]:
        """Todas las coincidencias del texto, de mayor a menor confianza."""
        if not texto:
            return []

        encontradas: dict[str, Coincidencia] = {}
        normalizado = normalizar_texto(texto)

        def registrar(coincidencia: Coincidencia) -> None:
            previa = encontradas.get(coincidencia.symbol)
            if previa is None or coincidencia.confianza > previa.confianza:
                encontradas[coincidencia.symbol] = coincidencia

        # 1. Cashtag: el autor dijo explicitamente de que ticker habla.
        for bruto in _CASHTAG.findall(texto):
            symbol = bruto.upper()
            if symbol in self.symbols:
                registrar(Coincidencia(symbol, "cashtag", 1.0, f"${symbol}"))

        # 2. Alias curado.
        for alias, symbol in self._alias.items():
            if _contiene_frase(normalizado, alias):
                registrar(Coincidencia(symbol, "alias", 0.9, alias))

        # 3. Nombre de empresa normalizado.
        for nombre, symbol in self._por_nombre.items():
            if _contiene_frase(normalizado, nombre):
                registrar(Coincidencia(symbol, "nombre", 0.8, nombre))

        # 4. Simbolo suelto, solo si no es ambiguo.
        for bruto in _SIMBOLO_SUELTO.findall(texto):
            symbol = bruto.upper()
            if symbol in self.symbols and symbol not in SIMBOLOS_AMBIGUOS:
                registrar(Coincidencia(symbol, "simbolo_suelto", 0.6, symbol))

        return sorted(encontradas.values(), key=lambda c: (-c.confianza, c.symbol))

    def resolver_uno(self, texto: str) -> Coincidencia | None:
        """El ticker mas probable, o None si no hay uno claramente dominante.

        Si dos tickers empatan en confianza, se descarta: un titular que
        menciona a dos empresas por igual no es evidencia sobre ninguna.
        """
        coincidencias = self.resolver(texto)
        if not coincidencias:
            return None
        if len(coincidencias) > 1 and coincidencias[0].confianza == coincidencias[1].confianza:
            return None
        return coincidencias[0]


def _contiene_frase(texto_normalizado: str, frase: str) -> bool:
    """Busca `frase` como palabras completas dentro del texto normalizado."""
    return f" {frase} " in f" {texto_normalizado} "


async def construir_resolutor(sesion: object) -> ResolutorEntidades:
    """Arma el resolutor con los tickers de la whitelist."""
    import sqlalchemy as sa

    from investing_bot.modelos.ticker import Ticker

    filas = (
        await sesion.execute(  # type: ignore[attr-defined]
            sa.select(Ticker.symbol, Ticker.nombre).where(Ticker.en_whitelist.is_(True))
        )
    ).all()
    return ResolutorEntidades(dict(filas))
