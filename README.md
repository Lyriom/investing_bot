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

## Estado: FASES 0, 1 y 3 — falta la 2

| | Componente | Estado |
|---|---|---|
| ✅ | Docker Compose, esquema de 11 tablas, Alembic, imagen multi-stage | FASE 0 |
| ✅ | Los cuatro ingestores: precios, noticias, Reddit, Congreso | FASE 1 |
| ✅ | Resolución de entidades y deduplicación de noticias | FASE 1 |
| ✅ | Sentimiento con FinBERT (extra opcional) y respaldo léxico | FASE 1 |
| ✅ | Capa point-in-time: toda lectura filtra por `observed_at` | FASE 3 |
| ✅ | Las cuatro señales, el motor de combinación y el gestor de riesgo | FASE 3 |
| ✅ | Digest diario a las 18:15 ET y los seis comandos del bot | FASE 3 |
| ⛔ | **Backtester** | **FASE 2, pendiente** |
| ⬜ | Portafolio sombra medido durante 3 meses | FASE 4 |

> ⚠ **Las señales no han pasado por ningún backtest.** Se construyó la FASE 3 antes que la
> FASE 2 a petición explícita del operador. Nadie ha comprobado todavía que estas señales
> superen a comprar SPY y quedarse quieto, ni qué les pasa con un día de retraso en la
> ejecución. El digest lo advierte en cada envío. **No operes con dinero real hasta correr
> la FASE 2.**

Los pesos (0.40 / 0.25 / 0.15) son provisionales y arbitrarios, como manda el invariante I4.

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

- **Easypanel** (u otro panel donde cada servicio es un contenedor suelto):
  [`docs/easypanel.md`](docs/easypanel.md).
- **VPS con Docker Compose**: [`docs/despliegue.md`](docs/despliegue.md). En corto:

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
investing-bot migrar                      # aplica migraciones de Alembic
investing-bot sembrar                     # carga la whitelist de instrumentos
investing-bot ingesta precios --dias 400  # un ingestor a mano (o `todos`)
investing-bot digest                      # genera el digest del dia
investing-bot digest --enviar             # lo manda por Telegram
investing-bot api                         # dashboard
investing-bot bot                         # bot de Telegram (polling)
investing-bot planificador                # APScheduler
investing-bot todo                        # los tres en un solo proceso
```

`todo` supervisa los tres servicios en un proceso: útil en paneles donde cada
servicio es un contenedor que hay que configurar a mano. Si uno cae, el proceso
termina con código distinto de cero para que el orquestador lo reinicie entero
—se cambia aislamiento por una sola configuración que mantener.

### Para que el sistema produzca algo

Con las claves vacías el digest sale **sin sugerencias**, y lo dice. Cada clave habilita
una señal:

| Clave | Señal que habilita | Peso |
|---|---|---|
| `FINNHUB_API_KEY` ([gratis](https://finnhub.io/register)) | S1, deriva post-noticia | 0.40 |
| `MARKETAUX_API_KEY` ([gratis](https://www.marketaux.com/register)) | Respaldo de S1 | — |
| `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` ([app tipo script](https://www.reddit.com/prefs/apps)) | S2, velocidad de menciones | 0.25 |
| — | S3, consenso del Congreso | 0.15 |

**Marketaux es respaldo, no una segunda opinión.** Solo se consulta para los tickers donde
Finnhub no trajo nada o falló, y alimenta la misma señal S1: dos agregadores de titulares
cubren en buena medida las mismas fuentes y no son independientes entre sí. Contarlos por
separado sería el anti-objetivo del SPEC. Su plan gratuito da 100 peticiones al día y 3
artículos por petición; el ingestor se limita solo a 90.

**S2 depende de una aprobación manual.** Desde junio de 2026 crear la app de Reddit no da
acceso a la API: hay que solicitarlo aparte bajo la Responsible Builder Policy, con colas de
semanas y rechazos frecuentes para proyectos personales.

**S3 no funciona hoy**: los datasets públicos de stock-watcher devuelven 403 desde agosto
de 2026. El ingestor está construido y probado; falta una fuente viva. Las URLs son
configurables (`URL_CONGRESO_CAMARA`, `URL_CONGRESO_SENADO`) para apuntar a un espejo.

Con S2 y S3 caídas el sistema funciona con **una sola fuente**. Sigue produciendo
sugerencias —el máximo alcanzable es 75/100, sobre el umbral de 60— pero cada una vendría de
un solo titular, no del cruce de fuentes independientes que supone el diseño.

Necesitas además **400 días de precios**, no 90: la señal de régimen compara SPY contra su
media de 200 días, y sin esas barras el sistema entra en modo defensivo y no sugiere nada.

## Desarrollo local

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 215 tests, sobre sqlite en memoria: sin contenedor y sin red
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
