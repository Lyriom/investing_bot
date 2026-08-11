"""Tests del dashboard. Se ejecuta la app ASGI en el mismo loop del test."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.modelos import PrecioDiario, Ticker
from investing_bot.web.app import crear_app


@pytest.fixture
def cliente(motor: object) -> AsyncClient:  # noqa: ARG001 - el fixture configura el motor global
    """Cliente HTTP contra la app ASGI, sin levantar un servidor."""
    return AsyncClient(transport=ASGITransport(app=crear_app()), base_url="http://test")


async def test_salud_responde_ok(cliente: AsyncClient) -> None:
    async with cliente:
        respuesta = await cliente.get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok"}


async def test_el_panel_renderiza_con_la_base_vacia(cliente: AsyncClient) -> None:
    async with cliente:
        respuesta = await cliente.get("/")
    assert respuesta.status_code == 200
    assert "investing_bot" in respuesta.text
    assert "Estado del pipeline" in respuesta.text


async def test_el_panel_lista_los_tickers_y_su_cobertura(
    cliente: AsyncClient, sesion: AsyncSession
) -> None:
    sesion.add(Ticker(symbol="AAPL", nombre="Apple Inc.", sector="Tecnologia", en_whitelist=True))
    sesion.add(
        PrecioDiario(
            symbol="AAPL",
            fecha=date(2026, 8, 10),
            cierre=Decimal("178.40"),
            observed_at=datetime(2026, 8, 10, 21, 0, tzinfo=UTC),
        )
    )
    await sesion.commit()

    async with cliente:
        respuesta = await cliente.get("/")
    assert "AAPL" in respuesta.text
    assert "Apple Inc." in respuesta.text


async def test_el_detalle_de_precios_muestra_observed_at(
    cliente: AsyncClient, sesion: AsyncSession
) -> None:
    sesion.add(Ticker(symbol="AAPL", en_whitelist=True))
    sesion.add(
        PrecioDiario(
            symbol="AAPL",
            fecha=date(2026, 8, 10),
            cierre=Decimal("178.40"),
            observed_at=datetime(2026, 8, 10, 21, 0, tzinfo=UTC),
        )
    )
    await sesion.commit()

    async with cliente:
        respuesta = await cliente.get("/precios/AAPL")
    assert respuesta.status_code == 200
    assert "178.40" in respuesta.text
    assert "observed_at" in respuesta.text


async def test_un_ticker_sin_precios_devuelve_404(cliente: AsyncClient) -> None:
    async with cliente:
        respuesta = await cliente.get("/precios/NOEXISTE")
    assert respuesta.status_code == 404
