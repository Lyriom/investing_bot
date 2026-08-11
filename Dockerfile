FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias de sistema minimas. curl se usa en el healthcheck del servicio web.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Primero solo los metadatos del proyecto, para aprovechar la cache de capas
# cuando cambia el codigo pero no las dependencias.
COPY pyproject.toml README.md ./
COPY src/investing_bot/__init__.py src/investing_bot/__init__.py
RUN pip install --upgrade pip && pip install -e ".[dev]"

# Ahora si, el resto del proyecto.
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY tests ./tests
COPY docs ./docs

RUN useradd --create-home --uid 1000 investing \
    && chown -R investing:investing /app \
    # yfinance crea su cache de zonas horarias desde varios hilos a la vez y
    # cada uno grita si otro se le adelanto. Creando el directorio antes, la
    # carrera no ocurre y los logs quedan limpios.
    && mkdir -p /home/investing/.cache/py-yfinance \
    && chown -R investing:investing /home/investing/.cache
USER investing

EXPOSE 8000

CMD ["investing-bot", "api"]
