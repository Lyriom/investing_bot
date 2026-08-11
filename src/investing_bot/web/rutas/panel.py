"""Rutas del panel de control."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from investing_bot.config import obtener_config
from investing_bot.db import sesion_bd
from investing_bot.servicios.consultas import (
    conteos_generales,
    estado_ingestores,
    resumen_tickers,
    ultimos_precios,
)

RUTA_PLANTILLAS = Path(__file__).resolve().parent.parent / "plantillas"
plantillas = Jinja2Templates(directory=str(RUTA_PLANTILLAS))

router = APIRouter()


async def dependencia_sesion() -> AsyncIterator[AsyncSession]:
    """Sesion de base de datos por request."""
    async with sesion_bd() as sesion:
        yield sesion


@router.get("/salud", response_class=JSONResponse)
async def salud(sesion: AsyncSession = Depends(dependencia_sesion)) -> dict[str, str]:
    """Healthcheck: confirma que la aplicacion responde y la BD contesta."""
    await sesion.execute(sa.text("SELECT 1"))
    return {"estado": "ok"}


@router.get("/", response_class=HTMLResponse)
async def inicio(
    request: Request, sesion: AsyncSession = Depends(dependencia_sesion)
) -> HTMLResponse:
    """Estado del pipeline y cobertura de datos."""
    config = obtener_config()
    return plantillas.TemplateResponse(
        request=request,
        name="panel.html",
        context={
            "entorno": config.entorno,
            "capital": config.capital_total_usd,
            "conteos": await conteos_generales(sesion),
            "ingestores": await estado_ingestores(sesion),
            "tickers": await resumen_tickers(sesion),
        },
    )


@router.get("/precios/{symbol}", response_class=HTMLResponse)
async def precios_de_ticker(
    request: Request,
    symbol: str,
    sesion: AsyncSession = Depends(dependencia_sesion),
) -> HTMLResponse:
    """Fragmento HTMX con las ultimas barras de un simbolo."""
    barras = await ultimos_precios(sesion, symbol, limite=30)
    if not barras:
        raise HTTPException(status_code=404, detail=f"Sin precios para {symbol}")
    return plantillas.TemplateResponse(
        request=request,
        name="parcial_precios.html",
        context={"symbol": symbol.upper(), "barras": barras},
    )
