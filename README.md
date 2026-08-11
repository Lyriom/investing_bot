# investing_bot

Sistema de **generación de señales de inversión** para acciones y ETFs de EE.UU.
Ingiere datos de fuentes independientes, los sintetiza en un score por ticker y entrega
un digest diario auditable por Telegram.

> **El sistema no ejecuta órdenes.** No hay código de ejecución, ni credenciales de bróker,
> ni automatización de ninguna app. La única salida son mensajes de Telegram y filas en la
> base de datos. El operador humano decide y ejecuta a mano.

La especificación completa está en [`docs/SPEC.md`](docs/SPEC.md) y es la fuente de verdad
del diseño. Las decisiones y resultados, incluidos los negativos, van a
[`docs/bitacora.md`](docs/bitacora.md).

---

## Estado: FASE 0 — Cimientos

El SPEC manda trabajar **una fase a la vez**. Lo implementado hoy:

| | Componente | Estado |
|---|---|---|
| ✅ | Docker Compose (postgres + migraciones + app + planificador + bot) | listo |
| ✅ | Esquema completo de las 11 tablas + Alembic | listo |
| ✅ | `config.py` con pydantic-settings | listo |
| ✅ | Ingestor de precios (yfinance), idempotente y tolerante a fallo | listo |
| ✅ | Bot de Telegram con `/start` y `/estado`, solo para el chat autorizado | listo |
| ✅ | Dashboard con estado del pipeline y cobertura de datos | listo |
| ✅ | Imagen multi-stage y compose de producción para el servidor | listo |
| ⬜ | Ingestores de noticias, Reddit y Congreso; normalizador; FinBERT | FASE 1 |
| ⬜ | Backtester point-in-time | FASE 2 |
| ⬜ | Señales, gestor de riesgo, digest diario | FASE 3 |

No hay señales ni sugerencias todavía, y eso es deliberado: el invariante I4 prohíbe
tocar pesos antes de que el backtester de la FASE 2 esté validado.

---

## Arranque local

Requisitos: Docker y Docker Compose. Nada más.

```bash
cp .env.example .env      # rellenar POSTGRES_PASSWORD y, si se quiere bot, las claves de Telegram
docker compose up --build
```

Eso levanta, en orden:

1. **`db`** — PostgreSQL 16, con healthcheck.
2. **`migraciones`** — aplica Alembic y siembra la whitelist de 30 instrumentos. Corre una vez y termina.
3. **`app`** — dashboard en <http://localhost:8000>.
4. **`planificador`** — APScheduler; ingesta de precios a las 17:00 ET, de lunes a viernes.
5. **`bot`** — Telegram. Si no hay token configurado, registra el motivo y sale sin error.

Para cargar el historial inicial de precios sin esperar al planificador:

```bash
docker compose run --rm app investing-bot ingesta precios --dias 90
```

### Variables de entorno

Todas están documentadas en [`.env.example`](.env.example). Las mínimas para arrancar:

| Variable | Para qué |
|---|---|
| `POSTGRES_PASSWORD` | Contraseña de la base |
| `TELEGRAM_BOT_TOKEN` | Token de @BotFather. Sin él, el servicio `bot` no arranca (y el resto sigue funcionando) |
| `TELEGRAM_CHAT_ID_AUTORIZADO` | Único chat que el bot atiende. Con `0` el bot se niega a arrancar |

**Los secretos nunca entran al repositorio** (invariante I5). `.env` está en `.gitignore`
y en `.dockerignore`, y hay un test que lo verifica.

---

## Despliegue en servidor

Guía completa en [`docs/despliegue.md`](docs/despliegue.md). En corto:

```bash
git clone https://github.com/Lyriom/investing_bot.git /opt/investing_bot
cd /opt/investing_bot && cp .env.example .env && chmod 600 .env   # y rellenarlo
docker compose -f docker-compose.produccion.yml up -d --build
```

El `Dockerfile` es multi-stage con dos destinos:

| Destino | Contiene | Lo usa |
|---|---|---|
| `desarrollo` | pytest, ruff, mypy, tests, instalación editable | `docker-compose.yml` |
| `produccion` | solo el venv de runtime + Alembic. 657 MB frente a 810 MB | `docker-compose.produccion.yml` |

`produccion` es el último stage y por tanto el destino por defecto de `docker build .`:
quien construya sin `--target` obtiene la imagen de servidor, no la de desarrollo.

> **El dashboard no tiene autenticación.** El compose de producción lo ata a `127.0.0.1`
> a propósito, y `db` no publica ningún puerto. El acceso pasa por un proxy inverso con
> TLS y contraseña, o por un túnel SSH. Publicar el 8000 en internet sería publicar tu
> portafolio.

---

## Comandos

```bash
investing-bot migrar                     # aplica migraciones de Alembic
investing-bot sembrar                    # carga la whitelist de instrumentos
investing-bot ingesta precios --dias 90  # corre el ingestor a mano
investing-bot api                        # dashboard
investing-bot bot                        # bot de Telegram (polling)
investing-bot planificador               # APScheduler
```

## Desarrollo local

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 77 tests, sobre sqlite en memoria: sin contenedor y sin red
ruff check . && ruff format --check .
mypy
```

La suite **nunca golpea APIs externas**: el ingestor recibe su descargador por inyección y
los tests le pasan DataFrames sintéticos deterministas.

---

## Decisiones de diseño que se apartan del SPEC

Las tres están justificadas y anotadas en la bitácora.

1. **`observed_at` en `precios_diarios` no es `now()`.** El SPEC dice que los ingestores
   escriben siempre `observed_at = now()`. Para barras diarias eso haría inservible cualquier
   carga histórica: 90 días de precios quedarían con la misma fecha de observación y el
   backtester no vería nada antes de hoy. Se usa el cierre oficial + 1 h, que es el instante
   más temprano en que el sistema *pudo* conocer la barra, y `creado_at` guarda aparte el
   momento real de la ingesta. Los precios no se corrigen retroactivamente, así que la
   equivalencia es segura. Para noticias, Reddit y Congreso **no** lo es, y ahí sí va `now()`.

2. **Dos tablas extra**: `corridas_ingesta` (la vista de estado del pipeline de la sección 6.7
   necesita saber cuándo corrió cada ingestor y qué falló) y `estado_sistema` (para que el chat
   vinculado y el kill switch sobrevivan a un reinicio de contenedor).

3. **`UNIQUE` del Congreso con `NULLS NOT DISTINCT`.** Buena parte de los trades llega sin
   ticker resuelto. Con la semántica SQL por defecto `NULL != NULL`, así que el UNIQUE del SPEC
   no dispararía y el ingestor perdería la idempotencia justo en las filas más difíciles.

Además hay un paquete `servicios/` que no figura en el árbol de la sección 4, para no duplicar
las mismas consultas de lectura entre el bot y el dashboard.

---

## Licencia

MIT.
