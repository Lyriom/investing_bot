# Imagen multi-stage con dos destinos:
#
#   --target desarrollo  -> incluye dependencias de dev, tests y instalacion
#                           editable. Lo usa docker-compose.yml.
#   --target produccion  -> solo lo que hace falta para correr. Sin tests, sin
#                           ruff/mypy/pytest, sin codigo fuente suelto. Lo usa
#                           docker-compose.produccion.yml.
#
# `produccion` es el destino por defecto: si alguien construye sin `--target`,
# lo que obtiene es la imagen de servidor, no la de desarrollo.

# =============================================================================
# base — lo comun a los dos destinos
# =============================================================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RUTA_VENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# curl es para el healthcheck del servicio web. Nada mas se instala aqui.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios. El directorio de cache de yfinance se crea de
# antemano porque la libreria lo crea desde varios hilos a la vez y cada uno
# grita si otro se le adelanto.
RUN useradd --create-home --uid 1000 investing \
    && mkdir -p /home/investing/.cache/py-yfinance \
    && chown -R investing:investing /home/investing

WORKDIR /app

# =============================================================================
# constructor — resuelve e instala las dependencias en un venv aislado
# =============================================================================
FROM base AS constructor

# El toolchain de compilacion vive solo en esta capa: nunca llega a la imagen
# final, que es lo que mantiene el resultado pequeno y con menos superficie.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$RUTA_VENV"

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip setuptools wheel \
    && pip install .

# =============================================================================
# desarrollo — reutiliza el venv y le agrega el instrumental de trabajo
# =============================================================================
FROM base AS desarrollo

COPY --from=constructor "$RUTA_VENV" "$RUTA_VENV"

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY tests ./tests
COPY docs ./docs

# Instalacion editable + extras de dev. Las dependencias pesadas ya estan en el
# venv heredado, asi que este paso es rapido.
RUN pip install -e ".[dev]" \
    && chown -R investing:investing /app

USER investing

EXPOSE 8000

CMD ["investing-bot", "api"]

# =============================================================================
# produccion — imagen de servidor
# =============================================================================
FROM base AS produccion

LABEL org.opencontainers.image.title="investing_bot" \
      org.opencontainers.image.description="Generacion de senales de inversion. No ejecuta ordenes." \
      org.opencontainers.image.source="https://github.com/Lyriom/investing_bot" \
      org.opencontainers.image.licenses="MIT"

# El paquete ya vive dentro del venv: no se copia `src/`. Lo unico que hace
# falta aparte es Alembic, porque las migraciones se aplican desde el contenedor.
COPY --from=constructor "$RUTA_VENV" "$RUTA_VENV"
COPY alembic.ini ./
COPY alembic ./alembic

RUN chown -R investing:investing /app
USER investing

EXPOSE 8000

# Sin HEALTHCHECK en la imagen: de los cuatro procesos que salen de aqui
# (api, bot, planificador, migraciones) solo uno sirve HTTP. Marcar a los otros
# tres como "unhealthy" seria ruido. El healthcheck va por servicio en compose.

CMD ["investing-bot", "api"]
