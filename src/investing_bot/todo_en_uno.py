"""Los tres servicios en un solo proceso.

El despliegue normal separa API, planificador y bot en tres contenedores. Para
un sistema de un solo operador eso es ceremonia: tres servicios que configurar,
tres juegos de variables que mantener en sincronia y tres sitios donde
equivocarse. Este modo los corre juntos, supervisados, en un contenedor.

Lo que se pierde al juntarlos: si uno cae, caen los tres, porque el proceso
termina con codigo distinto de cero para que el orquestador lo reinicie entero.
Es un intercambio consciente — un reinicio de treinta segundos a cambio de no
tener que mantener tres configuraciones. Con mas de un operador, o si el
dashboard tiene que seguir en pie mientras se depura el bot, conviene volver a
separarlos.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine, Iterator, Mapping
from typing import Any

import uvicorn

from investing_bot.apagado import instalar_senales
from investing_bot.config import Configuracion, obtener_config
from investing_bot.db import cerrar_motor
from investing_bot.planificador import correr_planificador
from investing_bot.registro import obtener_logger

log = obtener_logger(__name__)

Servicio = Callable[[asyncio.Event], Coroutine[Any, Any, None]]

SEGUNDOS_APAGADO = 30.0
"""Margen por servicio antes de cancelarlo a la fuerza.

Generoso a proposito: `planificador.shutdown(wait=True)` puede estar esperando
a que termine una ingesta.
"""


class ServidorSinSenales(uvicorn.Server):
    """Uvicorn sin manejadores de senales propios.

    `Server.serve()` instala los suyos con `signal.signal`, los restaura al
    salir y **vuelve a lanzar la senal capturada**. En un proceso donde uvicorn
    es uno de tres servicios eso mataria al bot y al planificador a mitad del
    cierre. Aqui las senales las maneja el supervisor, y solo el.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def correr_api(detener: asyncio.Event) -> None:
    """Sirve el dashboard hasta que se pida el apagado."""
    config = obtener_config()
    servidor = ServidorSinSenales(
        uvicorn.Config(
            "investing_bot.web.app:app",
            host=config.web_host,
            port=config.web_puerto,
            log_config=None,
        )
    )
    servir = asyncio.create_task(servidor.serve(), name="uvicorn")
    espera = asyncio.create_task(detener.wait(), name="espera_api")
    try:
        await asyncio.wait({servir, espera}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        espera.cancel()
        servidor.should_exit = True
        await servir  # propaga la excepcion si uvicorn murio por su cuenta


def servicios_por_defecto(config: Configuracion) -> dict[str, Servicio]:
    """Arma los servicios a supervisar, en orden de arranque.

    El orden importa para el apagado, que va en sentido inverso: la API se
    cierra la ultima porque su ciclo de vida cierra el pool de conexiones que
    los otros dos siguen usando mientras terminan.
    """
    servicios: dict[str, Servicio] = {}

    if config.telegram_configurado:
        from investing_bot.telegram.bot import correr_bot

        servicios["bot"] = correr_bot
    else:
        from investing_bot.telegram.bot import avisar_si_no_configurado

        avisar_si_no_configurado(config)

    servicios["planificador"] = correr_planificador
    servicios["api"] = correr_api
    return servicios


async def _cerrar(nombre: str, tarea: asyncio.Task[None]) -> int:
    """Espera a que un servicio cierre. Devuelve 1 si no cerro limpio."""
    try:
        await asyncio.wait_for(asyncio.shield(tarea), timeout=SEGUNDOS_APAGADO)
    except TimeoutError:
        log.warning("servicio_no_cerro_a_tiempo", servicio=nombre, plazo_seg=SEGUNDOS_APAGADO)
        tarea.cancel()
        with contextlib.suppress(BaseException):
            await tarea
        return 1
    except asyncio.CancelledError:
        return 0
    except Exception as exc:  # noqa: BLE001 - se reporta y se sigue cerrando el resto
        log.error("servicio_caido", servicio=nombre, error=str(exc), exc_info=exc)
        return 1
    return 0


async def ejecutar_todo(servicios: Mapping[str, Servicio] | None = None) -> int:
    """Corre todos los servicios en paralelo. Devuelve el codigo de salida.

    Sale en cuanto llega una senal o en cuanto uno de los servicios termina por
    su cuenta — ninguno de los tres deberia hacerlo, asi que eso es un fallo y
    se reporta como tal.
    """
    config = obtener_config()
    detener = asyncio.Event()
    instalar_senales(detener)

    activos = dict(servicios) if servicios is not None else servicios_por_defecto(config)
    tareas: dict[str, asyncio.Task[None]] = {
        nombre: asyncio.create_task(fn(detener), name=nombre) for nombre, fn in activos.items()
    }
    log.info("todo_en_uno_iniciado", servicios=list(tareas))

    espera = asyncio.create_task(detener.wait(), name="espera_supervisor")
    await asyncio.wait({*tareas.values(), espera}, return_when=asyncio.FIRST_COMPLETED)
    espera.cancel()

    # `detener` distingue las dos formas de despertar. Si ya esta puesto, el
    # apagado lo pidio una senal y los servicios estan terminando porque se les
    # dijo: no hay nada que reportar. Si no lo esta, alguno se fue por su
    # cuenta, y ninguno de los tres deberia hacerlo.
    codigo = 0
    if not detener.is_set():
        prematuros = [nombre for nombre, tarea in tareas.items() if tarea.done()]
        log.error("servicio_termino_antes_de_tiempo", servicios=prematuros)
        codigo = 1

    detener.set()
    for nombre in reversed(list(tareas)):
        codigo = max(codigo, await _cerrar(nombre, tareas[nombre]))

    await cerrar_motor()
    log.info("todo_en_uno_detenido", codigo=codigo)
    return codigo
