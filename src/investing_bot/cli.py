"""Punto de entrada por linea de comandos.

investing-bot migrar        aplica las migraciones de Alembic
investing-bot sembrar       carga la whitelist inicial de instrumentos
investing-bot ingesta       corre un ingestor a mano
investing-bot api           levanta el dashboard
investing-bot bot           levanta el bot de Telegram
investing-bot planificador  levanta APScheduler
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from investing_bot import __version__
from investing_bot.config import RAIZ_PROYECTO, obtener_config
from investing_bot.db import cerrar_motor, sesion_bd
from investing_bot.ingestores import INGESTORES
from investing_bot.registro import configurar_logs, obtener_logger

log = obtener_logger(__name__)

INGESTORES_DISPONIBLES = tuple(INGESTORES)


def _localizar_alembic_ini() -> Path:
    """Encuentra `alembic.ini` tanto en el contenedor como en el checkout local."""
    for candidato in (RAIZ_PROYECTO / "alembic.ini", Path.cwd() / "alembic.ini"):
        if candidato.is_file():
            return candidato
    raise FileNotFoundError(
        "No se encontro alembic.ini. Corre el comando desde la raiz del proyecto."
    )


def comando_migrar(_: argparse.Namespace) -> int:
    """Aplica las migraciones pendientes hasta `head`."""
    from alembic import command
    from alembic.config import Config

    ini = _localizar_alembic_ini()
    configuracion = Config(str(ini))
    configuracion.set_main_option("script_location", str(ini.parent / "alembic"))
    log.info("aplicando_migraciones", alembic_ini=str(ini))
    command.upgrade(configuracion, "head")
    log.info("migraciones_aplicadas")
    return 0


def comando_esperar_bd(args: argparse.Namespace) -> int:
    """Bloquea hasta que PostgreSQL acepte consultas, o hasta agotar el plazo.

    En Docker Compose esto lo resuelve `depends_on: service_healthy`, pero en
    plataformas donde cada servicio es un contenedor suelto (Easypanel,
    Railway) no hay tal garantia: sin esta espera, el contenedor arranca antes
    que la base, revienta y entra en un ciclo de reinicios.
    """
    import sqlalchemy as sa

    async def ejecutar() -> int:
        limite = time.monotonic() + args.timeout
        intento = 0
        while True:
            intento += 1
            try:
                async with sesion_bd() as sesion:
                    await sesion.execute(sa.text("SELECT 1"))
                log.info("bd_disponible", intentos=intento)
                return 0
            except Exception as exc:  # noqa: BLE001 - se reintenta hasta el plazo
                if time.monotonic() >= limite:
                    log.error(
                        "bd_no_disponible",
                        intentos=intento,
                        timeout_seg=args.timeout,
                        error=str(exc),
                    )
                    return 1
                log.info("esperando_bd", intento=intento, error=type(exc).__name__)
                await asyncio.sleep(args.intervalo)

    try:
        return asyncio.run(ejecutar())
    finally:
        asyncio.run(cerrar_motor())


def comando_sembrar(args: argparse.Namespace) -> int:
    """Carga la whitelist inicial en la tabla `tickers`."""
    from investing_bot.servicios.siembra import sembrar_whitelist

    async def ejecutar() -> None:
        try:
            async with sesion_bd() as sesion:
                await sembrar_whitelist(sesion, args.archivo)
        finally:
            await cerrar_motor()

    asyncio.run(ejecutar())
    return 0


def comando_ingesta(args: argparse.Namespace) -> int:
    """Corre uno o todos los ingestores de forma manual."""

    async def ejecutar() -> int:
        nombres = list(INGESTORES) if args.nombre == "todos" else [args.nombre]
        try:
            fallos = 0
            for nombre in nombres:
                clase = INGESTORES[nombre]
                # `dias` solo lo aceptan los ingestores con ventana temporal.
                ingestor = clase(dias=args.dias) if args.dias is not None else clase()  # type: ignore[call-arg]
                resultado = await ingestor.ejecutar_registrado()
                if not resultado.exito:
                    fallos += 1
            return 1 if fallos == len(nombres) else 0
        finally:
            await cerrar_motor()

    return asyncio.run(ejecutar())


def comando_digest(args: argparse.Namespace) -> int:
    """Genera el digest del dia. Con --enviar lo manda por Telegram."""
    from datetime import date as _date

    from investing_bot.servicios.digest import (
        ejecutar_digest_diario,
        generar_digest,
        persistir_digest,
    )

    fecha = _date.fromisoformat(args.fecha) if args.fecha else _date.today()

    async def ejecutar() -> int:
        try:
            if args.enviar:
                resultado = await ejecutar_digest_diario(fecha)
            else:
                async with sesion_bd() as sesion:
                    resultado = await generar_digest(sesion, fecha)
                    if args.persistir:
                        await persistir_digest(sesion, resultado)
            log.info("digest_generado", fecha=str(fecha), enviado=resultado.enviado)
            # El digest es para leerlo: va por stdout aparte de los logs.
            sys.stdout.write(resultado.texto + "\n")
            return 0
        finally:
            await cerrar_motor()

    return asyncio.run(ejecutar())


def comando_api(_: argparse.Namespace) -> int:
    """Levanta el dashboard con uvicorn."""
    import uvicorn

    config = obtener_config()
    uvicorn.run(
        "investing_bot.web.app:app",
        host=config.web_host,
        port=config.web_puerto,
        log_config=None,
    )
    return 0


def comando_bot(_: argparse.Namespace) -> int:
    """Levanta el bot de Telegram en modo polling."""
    from investing_bot.telegram.bot import ejecutar_bot

    ejecutar_bot()
    return 0


def comando_planificador(_: argparse.Namespace) -> int:
    """Levanta APScheduler."""
    from investing_bot.planificador import ejecutar_planificador

    asyncio.run(ejecutar_planificador())
    return 0


def construir_parser() -> argparse.ArgumentParser:
    """Define la interfaz de linea de comandos."""
    parser = argparse.ArgumentParser(
        prog="investing-bot",
        description="Generacion de senales de inversion. El sistema no ejecuta ordenes.",
    )
    parser.add_argument("--version", action="version", version=f"investing-bot {__version__}")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("migrar", help="Aplica las migraciones de Alembic").set_defaults(
        funcion=comando_migrar
    )

    p_esperar = sub.add_parser("esperar-bd", help="Espera a que PostgreSQL acepte consultas")
    p_esperar.add_argument("--timeout", type=float, default=90.0, help="Plazo maximo en segundos")
    p_esperar.add_argument("--intervalo", type=float, default=2.0, help="Segundos entre intentos")
    p_esperar.set_defaults(funcion=comando_esperar_bd)

    p_sembrar = sub.add_parser("sembrar", help="Carga la whitelist inicial de instrumentos")
    p_sembrar.add_argument("--archivo", type=Path, default=None, help="Ruta a un whitelist.json")
    p_sembrar.set_defaults(funcion=comando_sembrar)

    p_ingesta = sub.add_parser("ingesta", help="Corre un ingestor a mano")
    p_ingesta.add_argument("nombre", choices=(*INGESTORES_DISPONIBLES, "todos"))
    p_ingesta.add_argument(
        "--dias", type=int, default=None, help="Dias de historial a traer (por defecto, config)"
    )
    p_ingesta.set_defaults(funcion=comando_ingesta)

    p_digest = sub.add_parser("digest", help="Genera el digest diario")
    p_digest.add_argument("--fecha", default=None, help="AAAA-MM-DD (por defecto, hoy)")
    p_digest.add_argument("--enviar", action="store_true", help="Enviarlo por Telegram")
    p_digest.add_argument("--persistir", action="store_true", help="Guardar senales y sugerencias")
    p_digest.set_defaults(funcion=comando_digest)

    sub.add_parser("api", help="Levanta el dashboard").set_defaults(funcion=comando_api)
    sub.add_parser("bot", help="Levanta el bot de Telegram").set_defaults(funcion=comando_bot)
    sub.add_parser("planificador", help="Levanta APScheduler").set_defaults(
        funcion=comando_planificador
    )
    return parser


def principal(argv: list[str] | None = None) -> int:
    """Punto de entrada del ejecutable `investing-bot`."""
    config = obtener_config()
    configurar_logs(config.nivel_log, config.entorno)
    args = construir_parser().parse_args(argv)
    return int(args.funcion(args))


if __name__ == "__main__":
    sys.exit(principal())
