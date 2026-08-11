"""Tests del ingestor de noticias. Sin red y sin claves.

Los descargadores se inyectan: la suite nunca llama a Finnhub ni a Marketaux.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investing_bot.config import Configuracion
from investing_bot.db import ahora_utc
from investing_bot.ingestores.noticias import (
    IngestorNoticias,
    PresupuestoDiario,
    Proveedor,
    descargar_marketaux,
    normalizar_articulo_marketaux,
    reiniciar_presupuesto_marketaux,
)
from investing_bot.modelos import Noticia, Ticker
from investing_bot.nlp.sentimiento import ClasificadorSentimiento

CLASIFICADOR = ClasificadorSentimiento(forzar_lexico=True)


async def _sembrar_tickers(fabrica: async_sessionmaker[AsyncSession], *symbols: str) -> None:
    async with fabrica() as sesion:
        for symbol in symbols:
            sesion.add(
                Ticker(symbol=symbol, nombre=f"{symbol} Inc", en_whitelist=True, activo=True)
            )
        await sesion.commit()


def _titular(titulo: str, horas_atras: int = 48, fuente: str = "Reuters") -> dict[str, Any]:
    momento = ahora_utc() - timedelta(hours=horas_atras)
    return {
        "headline": titulo,
        "summary": "Resumen del titular.",
        "url": f"https://ejemplo.test/{abs(hash(titulo))}",
        "source": fuente,
        "datetime": int(momento.timestamp()),
    }


def _descargador(*titulares: dict[str, Any]):
    """Descargador que siempre devuelve lo mismo y cuenta sus llamadas."""
    llamadas: list[str] = []

    async def descargar(symbol: str, desde: str, hasta: str, api_key: str) -> list[dict[str, Any]]:
        llamadas.append(symbol)
        return list(titulares)

    descargar.llamadas = llamadas  # type: ignore[attr-defined]
    return descargar


def _descargador_roto(excepcion: type[Exception] = RuntimeError):
    llamadas: list[str] = []

    async def descargar(symbol: str, desde: str, hasta: str, api_key: str) -> list[dict[str, Any]]:
        llamadas.append(symbol)
        raise excepcion("la fuente no responde")

    descargar.llamadas = llamadas  # type: ignore[attr-defined]
    return descargar


def _ingestor(
    fabrica: async_sessionmaker[AsyncSession], *proveedores: Proveedor
) -> IngestorNoticias:
    ingestor = IngestorNoticias(
        fabrica_sesiones=fabrica,
        proveedores=list(proveedores),
        clasificador=CLASIFICADOR,
        pausa_seg=0.0,
    )
    # Sin esto cada proveedor roto duerme el backoff real y la suite tarda
    # minutos. La politica de reintentos se prueba en test_utilidades.
    ingestor.reintentos = 1
    ingestor.backoff_base_seg = 0.0
    return ingestor


@pytest.fixture(autouse=True)
def _presupuesto_limpio():
    """El presupuesto es de proceso: sin esto un test contaminaria al siguiente."""
    reiniciar_presupuesto_marketaux()
    yield
    reiniciar_presupuesto_marketaux()


# --- Camino feliz ----------------------------------------------------------


async def test_guarda_las_noticias_y_las_clasifica(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    await _sembrar_tickers(fabrica, "NVDA")
    principal = Proveedor("finnhub", _descargador(_titular("NVDA beats earnings")), "clave")

    resultado = await _ingestor(fabrica, principal).ejecutar()

    assert resultado.exito
    assert resultado.filas_nuevas == 1
    async with fabrica() as sesion:
        noticia = await sesion.scalar(sa.select(Noticia))
        assert noticia is not None
        assert noticia.symbol == "NVDA"
        assert noticia.sentimiento is not None
        assert noticia.modelo_usado


async def test_el_mismo_titular_no_se_guarda_dos_veces(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    await _sembrar_tickers(fabrica, "NVDA")
    principal = Proveedor("finnhub", _descargador(_titular("NVDA beats earnings")), "clave")

    await _ingestor(fabrica, principal).ejecutar()
    segunda = await _ingestor(fabrica, principal).ejecutar()

    assert segunda.filas_nuevas == 0
    assert segunda.filas_sin_cambios == 1


async def test_sin_proveedores_falla_con_un_mensaje_util(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    await _sembrar_tickers(fabrica, "NVDA")

    resultado = await _ingestor(fabrica).ejecutar()

    assert not resultado.exito
    assert "FINNHUB_API_KEY" in resultado.errores[0]


# --- Respaldo --------------------------------------------------------------


async def test_si_el_principal_falla_entra_el_respaldo(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    await _sembrar_tickers(fabrica, "NVDA")
    roto = _descargador_roto()
    respaldo = _descargador(_titular("NVDA beats earnings"))

    resultado = await _ingestor(
        fabrica,
        Proveedor("finnhub", roto, "clave"),
        Proveedor("marketaux", respaldo, "clave2"),
    ).ejecutar()

    assert resultado.exito
    assert resultado.filas_nuevas == 1
    assert respaldo.llamadas == ["NVDA"]  # type: ignore[attr-defined]


async def test_que_el_principal_falle_no_es_error_si_el_respaldo_cubre(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    """El respaldo haciendo su trabajo no debe ensuciar la vista de estado."""
    await _sembrar_tickers(fabrica, "NVDA")

    resultado = await _ingestor(
        fabrica,
        Proveedor("finnhub", _descargador_roto(), "clave"),
        Proveedor("marketaux", _descargador(_titular("NVDA sube")), "clave2"),
    ).ejecutar()

    assert resultado.errores == []


async def test_si_caen_los_dos_se_registra_y_el_resto_sigue(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    await _sembrar_tickers(fabrica, "NVDA", "AMD")

    resultado = await _ingestor(
        fabrica,
        Proveedor("finnhub", _descargador_roto(), "clave"),
        Proveedor("marketaux", _descargador_roto(), "clave2"),
    ).ejecutar()

    assert not resultado.exito
    assert len(resultado.errores) == 4  # dos tickers x dos proveedores
    assert any("finnhub" in e for e in resultado.errores)
    assert any("marketaux" in e for e in resultado.errores)


async def test_el_respaldo_no_se_llama_si_el_principal_trajo_algo(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    """Cada llamada de mas gasta cuota diaria que puede hacer falta luego."""
    await _sembrar_tickers(fabrica, "NVDA")
    respaldo = _descargador(_titular("otro titular"))

    await _ingestor(
        fabrica,
        Proveedor("finnhub", _descargador(_titular("NVDA beats earnings")), "clave"),
        Proveedor("marketaux", respaldo, "clave2"),
    ).ejecutar()

    assert respaldo.llamadas == []  # type: ignore[attr-defined]


async def test_el_presupuesto_agotado_deja_fuera_al_respaldo(
    fabrica: async_sessionmaker[AsyncSession],
) -> None:
    await _sembrar_tickers(fabrica, "NVDA", "AMD", "TSLA")
    respaldo = _descargador(_titular("titular de respaldo"))
    presupuesto = PresupuestoDiario(maximo=2)

    await _ingestor(
        fabrica,
        Proveedor("finnhub", _descargador_roto(), "clave"),
        Proveedor("marketaux", respaldo, "clave2", presupuesto=presupuesto),
    ).ejecutar()

    assert len(respaldo.llamadas) == 2  # type: ignore[attr-defined]
    assert presupuesto.restantes == 0


# --- Presupuesto -----------------------------------------------------------


def test_el_presupuesto_cuenta_hacia_abajo() -> None:
    presupuesto = PresupuestoDiario(maximo=3)

    assert [presupuesto.consumir() for _ in range(4)] == [True, True, True, False]
    assert presupuesto.restantes == 0


def test_el_presupuesto_se_reinicia_al_cambiar_de_dia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presupuesto = PresupuestoDiario(maximo=1)
    assert presupuesto.consumir()
    assert not presupuesto.consumir()

    manana = ahora_utc() + timedelta(days=1)
    monkeypatch.setattr("investing_bot.ingestores.noticias.ahora_utc", lambda: manana)

    assert presupuesto.consumir()


# --- Normalizacion de Marketaux -------------------------------------------


def test_marketaux_se_traduce_al_formato_comun() -> None:
    articulo = {
        "uuid": "abc",
        "title": "NVIDIA beats earnings",
        "description": "Resultados por encima de lo esperado.",
        "url": "https://ejemplo.test/nvda",
        "source": "reuters.com",
        "published_at": "2026-08-10T14:30:00.000000Z",
        "entities": [{"symbol": "NVDA", "sentiment_score": 0.82}],
    }

    comun = normalizar_articulo_marketaux(articulo)

    assert comun is not None
    assert comun["headline"] == "NVIDIA beats earnings"
    assert comun["summary"] == "Resultados por encima de lo esperado."
    assert comun["source"] == "reuters.com"
    assert datetime.fromtimestamp(comun["datetime"], tz=UTC) == datetime(
        2026, 8, 10, 14, 30, tzinfo=UTC
    )


def test_el_sentimiento_de_marketaux_se_descarta() -> None:
    """El sistema clasifica con su propio modelo y anota cual uso (I3)."""
    comun = normalizar_articulo_marketaux(
        {
            "title": "NVIDIA beats earnings",
            "published_at": "2026-08-10T14:30:00Z",
            "entities": [{"symbol": "NVDA", "sentiment_score": 0.82}],
        }
    )

    assert comun is not None
    assert "sentiment_score" not in comun
    assert "sentimiento" not in comun


@pytest.mark.parametrize(
    "articulo",
    [
        {"title": "", "published_at": "2026-08-10T14:30:00Z"},
        {"title": "Sin fecha"},
        {"title": "Fecha ilegible", "published_at": "ayer por la tarde"},
    ],
)
def test_los_articulos_inservibles_se_descartan(articulo: dict[str, Any]) -> None:
    assert normalizar_articulo_marketaux(articulo) is None


# --- Descarga de Marketaux (sin red, con transporte simulado) --------------


def _cliente_simulado(monkeypatch: pytest.MonkeyPatch, carga: Any, estado: int = 200) -> list[Any]:
    """Sustituye el transporte de httpx. Devuelve las peticiones observadas."""
    vistas: list[httpx.Request] = []

    def manejar(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(estado, json=carga)

    original = httpx.AsyncClient

    def constructor(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(manejar)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", constructor)
    return vistas


async def test_descargar_marketaux_pide_y_traduce(monkeypatch: pytest.MonkeyPatch) -> None:
    vistas = _cliente_simulado(
        monkeypatch,
        {
            "meta": {"found": 1, "returned": 1, "limit": 3},
            "data": [
                {
                    "title": "NVIDIA beats earnings",
                    "description": "Buenos resultados.",
                    "url": "https://ejemplo.test/nvda",
                    "source": "reuters.com",
                    "published_at": "2026-08-10T14:30:00.000000Z",
                }
            ],
        },
    )

    articulos = await descargar_marketaux("NVDA", "2026-08-04", "2026-08-11", "clave")

    assert len(articulos) == 1
    assert articulos[0]["headline"] == "NVIDIA beats earnings"
    consulta = vistas[0].url.params
    assert consulta["symbols"] == "NVDA"
    assert consulta["api_token"] == "clave"
    assert consulta["published_after"].startswith("2026-08-04")


async def test_descargar_marketaux_tolera_una_respuesta_rara(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cliente_simulado(monkeypatch, {"error": {"code": "usage_limit_reached"}})

    assert await descargar_marketaux("NVDA", "2026-08-04", "2026-08-11", "clave") == []


async def test_descargar_marketaux_propaga_el_error_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un 402 por cuota agotada tiene que llegar al registro de la corrida."""
    _cliente_simulado(monkeypatch, {"error": "payment required"}, estado=402)

    with pytest.raises(httpx.HTTPStatusError):
        await descargar_marketaux("NVDA", "2026-08-04", "2026-08-11", "clave")


# --- Composicion de proveedores segun las claves --------------------------


@pytest.mark.parametrize(
    ("finnhub", "marketaux", "esperados"),
    [
        ("f", "m", ["finnhub", "marketaux"]),
        ("f", "", ["finnhub"]),
        ("", "m", ["marketaux"]),
        ("", "", []),
    ],
)
def test_las_claves_deciden_que_proveedores_se_arman(
    finnhub: str, marketaux: str, esperados: list[str]
) -> None:
    config = Configuracion(
        finnhub_api_key=finnhub,
        marketaux_api_key=marketaux,
        max_peticiones_marketaux_dia=90,
    )

    proveedores = IngestorNoticias._proveedores_configurados(config)

    assert [p.nombre for p in proveedores] == esperados


def test_solo_marketaux_tambien_es_una_configuracion_valida() -> None:
    """Si Reddit y el Congreso no estan, al menos que las noticias entren."""
    config = Configuracion(finnhub_api_key="", marketaux_api_key="m")

    proveedores = IngestorNoticias._proveedores_configurados(config)

    assert proveedores[0].presupuesto is not None
