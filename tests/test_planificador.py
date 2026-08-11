"""Tests del planificador, incluido el apagado ordenado que exige el servidor."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import pytest

from investing_bot.planificador import (
    HORA_INGESTA_PRECIOS,
    construir_planificador,
    ejecutar_planificador,
)


def _jobs() -> dict[str, Any]:
    """Jobs registrados. `construir_planificador` no arranca nada, solo declara."""
    return {job.id: job for job in construir_planificador().get_jobs()}


def test_se_registra_la_ingesta_diaria_de_precios() -> None:
    assert "ingesta_precios" in _jobs()


def test_la_ingesta_corre_tras_el_cierre_y_solo_en_dias_habiles() -> None:
    """17:00 ET: una hora despues del cierre, para no tomar barras parciales."""
    campos = {campo.name: str(campo) for campo in _jobs()["ingesta_precios"].trigger.fields}
    assert campos["hour"] == str(HORA_INGESTA_PRECIOS)
    assert campos["day_of_week"] == "mon-fri"


def test_la_ingesta_no_se_solapa_consigo_misma() -> None:
    """Dos ingestas simultaneas competirian por las mismas filas."""
    job = _jobs()["ingesta_precios"]
    assert job.max_instances == 1
    assert job.coalesce is True


def test_no_se_adelantan_jobs_de_fases_posteriores() -> None:
    """El digest diario y los otros ingestores son FASE 1 y 3."""
    assert list(_jobs()) == ["ingesta_precios"]


async def test_sigterm_apaga_el_planificador_sin_esperar_al_kill(motor: object) -> None:
    """`docker stop` manda SIGTERM: el proceso debe terminar por su cuenta.

    Sin manejador, Docker espera 10 s y recurre a SIGKILL en cada despliegue.
    """
    tarea = asyncio.create_task(ejecutar_planificador())
    await asyncio.sleep(0.2)  # deja que instale las senales y arranque
    os.kill(os.getpid(), signal.SIGTERM)

    try:
        await asyncio.wait_for(tarea, timeout=5)
    except TimeoutError:  # pragma: no cover
        tarea.cancel()
        pytest.fail("el planificador ignoro SIGTERM")
