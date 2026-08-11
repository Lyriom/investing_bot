"""Tests del supervisor que corre los tres servicios en un proceso."""

from __future__ import annotations

import asyncio
import signal

import pytest

from investing_bot.config import Configuracion
from investing_bot.todo_en_uno import (
    ServidorSinSenales,
    ejecutar_todo,
    servicios_por_defecto,
)


def _servicio_normal(registro: list[str], nombre: str):
    """Servicio bien portado: vive hasta que se pide el apagado y cierra."""

    async def correr(detener: asyncio.Event) -> None:
        registro.append(f"{nombre}:arranca")
        await detener.wait()
        registro.append(f"{nombre}:cierra")

    return correr


def _servicio_que_revienta(mensaje: str):
    async def correr(detener: asyncio.Event) -> None:
        await asyncio.sleep(0)
        raise RuntimeError(mensaje)

    return correr


def _servicio_colgado():
    """Ignora el apagado. Existe para probar que el supervisor lo cancela."""

    async def correr(detener: asyncio.Event) -> None:
        await asyncio.Event().wait()  # nunca se resuelve

    return correr


# --- Arranque y apagado ordenado ------------------------------------------


async def test_apagado_por_senal_cierra_todo_con_codigo_cero() -> None:
    registro: list[str] = []
    servicios = {
        "bot": _servicio_normal(registro, "bot"),
        "planificador": _servicio_normal(registro, "planificador"),
        "api": _servicio_normal(registro, "api"),
    }

    tarea = asyncio.create_task(ejecutar_todo(servicios))
    await asyncio.sleep(0.05)
    signal.raise_signal(signal.SIGTERM)

    codigo = await asyncio.wait_for(tarea, timeout=5)

    assert codigo == 0
    assert set(registro) == {
        "bot:arranca",
        "planificador:arranca",
        "api:arranca",
        "bot:cierra",
        "planificador:cierra",
        "api:cierra",
    }


async def test_la_api_se_cierra_la_ultima() -> None:
    """Su ciclo de vida cierra el pool que los otros dos siguen usando."""
    registro: list[str] = []
    servicios = {
        "bot": _servicio_normal(registro, "bot"),
        "planificador": _servicio_normal(registro, "planificador"),
        "api": _servicio_normal(registro, "api"),
    }

    tarea = asyncio.create_task(ejecutar_todo(servicios))
    await asyncio.sleep(0.05)
    signal.raise_signal(signal.SIGTERM)
    await asyncio.wait_for(tarea, timeout=5)

    cierres = [linea for linea in registro if linea.endswith(":cierra")]
    assert cierres[-1] == "api:cierra"


# --- Fallos ----------------------------------------------------------------


async def test_un_servicio_caido_baja_a_todos_con_codigo_uno() -> None:
    """El proceso muere entero para que el orquestador lo reinicie entero."""
    registro: list[str] = []
    servicios = {
        "bot": _servicio_que_revienta("token invalido"),
        "planificador": _servicio_normal(registro, "planificador"),
        "api": _servicio_normal(registro, "api"),
    }

    codigo = await asyncio.wait_for(ejecutar_todo(servicios), timeout=5)

    assert codigo == 1
    assert "planificador:cierra" in registro
    assert "api:cierra" in registro


async def test_un_servicio_que_termina_solo_tambien_es_un_fallo() -> None:
    """Ninguno de los tres deberia terminar por su cuenta sin senal."""
    registro: list[str] = []

    async def se_va_solo(detener: asyncio.Event) -> None:
        return

    servicios = {
        "bot": se_va_solo,
        "api": _servicio_normal(registro, "api"),
    }

    codigo = await asyncio.wait_for(ejecutar_todo(servicios), timeout=5)

    assert codigo == 1
    assert "api:cierra" in registro


async def test_un_servicio_colgado_se_cancela_y_no_bloquea_el_apagado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("investing_bot.todo_en_uno.SEGUNDOS_APAGADO", 0.1)
    registro: list[str] = []
    servicios = {
        "colgado": _servicio_colgado(),
        "api": _servicio_normal(registro, "api"),
    }

    tarea = asyncio.create_task(ejecutar_todo(servicios))
    await asyncio.sleep(0.05)
    signal.raise_signal(signal.SIGTERM)

    codigo = await asyncio.wait_for(tarea, timeout=5)

    assert codigo == 1
    assert "api:cierra" in registro


# --- Composicion de servicios ---------------------------------------------


def test_sin_telegram_configurado_no_se_incluye_el_bot() -> None:
    """El resto del sistema tiene que funcionar sin Telegram."""
    config = Configuracion(telegram_bot_token="", telegram_chat_id_autorizado=0)

    servicios = servicios_por_defecto(config)

    assert "bot" not in servicios
    assert set(servicios) == {"planificador", "api"}


def test_con_telegram_configurado_el_bot_arranca_primero() -> None:
    """El orden de insercion define el apagado inverso: la API, la ultima."""
    config = Configuracion(telegram_bot_token="123:abc", telegram_chat_id_autorizado=42)

    servicios = servicios_por_defecto(config)

    assert list(servicios) == ["bot", "planificador", "api"]


# --- Uvicorn ---------------------------------------------------------------


def test_uvicorn_no_toca_los_manejadores_de_senales() -> None:
    """Si los tocara, mataria al bot y al planificador a mitad del cierre."""
    import uvicorn

    servidor = ServidorSinSenales(uvicorn.Config("investing_bot.web.app:app"))
    antes = signal.getsignal(signal.SIGTERM)

    with servidor.capture_signals():
        assert signal.getsignal(signal.SIGTERM) is antes
