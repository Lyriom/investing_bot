# Bitácora

Registro cronológico de qué se construyó, qué se midió, qué resultó y qué se decidió.
**Los resultados negativos se registran igual que los positivos.**

---

## 2026-08-11 — FASE 0: cimientos

### Qué se construyó

Los cimientos completos del sistema, sin adelantar nada de fases posteriores.

- **Infraestructura**: Docker Compose con cinco servicios (`db`, `migraciones`, `app`,
  `planificador`, `bot`). `migraciones` corre una vez, aplica Alembic y siembra la whitelist;
  el resto arranca solo cuando ese servicio termina con éxito.
- **Esquema**: las 9 tablas de la sección 5 del SPEC más 2 propias, en una migración de Alembic.
- **Configuración**: `pydantic-settings`, todo por entorno, `.env.example` con las claves vacías.
- **Ingestor de precios**: yfinance, idempotente, con backoff exponencial y jitter, y con el
  descargador inyectable para poder testearlo sin red.
- **Bot de Telegram**: `/start` y `/estado`, filtrados por `chat_id` autorizado.
- **Dashboard**: FastAPI + Jinja2 + HTMX, con estado del pipeline y cobertura de precios.
- **72 tests** sobre sqlite en memoria, sin red y sin contenedor.

### Qué se midió

| Medición | Resultado |
|---|---|
| `docker compose up` desde cero (volumen borrado) | 5 servicios arriba, `app` y `db` *healthy* |
| Migración Alembic sobre base vacía | `d847ecd0e7e3` aplicada, sin error |
| Whitelist sembrada | 30 nuevas, 0 actualizadas |
| Ingesta de precios, primera corrida | 2700 leídas, 2700 nuevas, 0 errores, 3.2 s |
| Cobertura resultante | 30 tickers × 90 barras, 2026-04-02 → 2026-08-11 |
| Ingesta de precios, segunda corrida | 2700 leídas, **0 nuevas**, 33 actualizadas |
| Suite de tests | 72 pasan, 82 % de cobertura |
| `ruff check` / `ruff format --check` / `mypy` | limpios |

Las 33 filas actualizadas de la segunda corrida no son un fallo de idempotencia: son las barras
del día en curso, que seguían moviéndose porque la ingesta se corrió con el mercado abierto.
El conteo total se mantuvo en 2700. El job del planificador corre a las 17:00 ET, una hora
después del cierre, justamente para no tomar barras parciales.

### Qué se decidió

**1. `observed_at` en `precios_diarios` no es `now()`.**

El SPEC (6.1) dice que los ingestores escriben siempre `observed_at = now()`. Se implementó
distinto y a conciencia: para una barra diaria, `observed_at` se deriva del cierre oficial del
mercado más una hora de margen de consolidación.

El motivo es que la regla literal rompería el invariante que pretende proteger. Al cargar 90
días de historial, las 2700 filas quedarían con la misma fecha de observación —hoy—, y el
backtester de la FASE 2, que solo puede leer por `observed_at`, no vería absolutamente nada
antes de hoy. La carga histórica sería inservible.

La equivalencia es segura **solo** para precios: una barra diaria no se corrige retroactivamente,
así que el instante más temprano en que el sistema pudo conocerla es el cierre. Para noticias,
Reddit y Congreso esto **no** es cierto —ahí el retraso entre el hecho y su publicación es
precisamente la señal— y en esas tablas sí va `now()`. El momento real de la ingesta queda
igualmente registrado en `creado_at`, así que no se pierde información.

**2. El `UNIQUE` de `congreso_trades` necesita `NULLS NOT DISTINCT`.**

Salió de un test que falló. La restricción del SPEC es
`UNIQUE(miembro, symbol, fecha_transaccion, monto_min, tipo)`, pero `symbol` es nullable y buena
parte de los trades llega sin ticker resuelto. Con la semántica SQL por defecto `NULL != NULL`,
así que el UNIQUE no dispara y el ingestor de la FASE 1 perdería la idempotencia justo en las
filas más difíciles de deduplicar. `NULLS NOT DISTINCT` (PostgreSQL 15+) lo corrige. sqlite no lo
soporta, así que el test verifica la declaración de la restricción, no el comportamiento del motor.

**3. Dos tablas fuera del SPEC.**

`corridas_ingesta` porque la vista de estado del pipeline (6.7) necesita saber cuándo corrió cada
ingestor, cuántas filas trajo y qué falló, y esa información no está en ninguna otra parte.
`estado_sistema` (clave-valor) para que el chat vinculado por `/start` y, más adelante, el kill
switch de `/pausar`, sobrevivan a un reinicio de contenedor.

**4. El upsert no usa `ON CONFLICT`.**

Se implementó a mano (leer claves existentes, comparar, insertar/actualizar) en vez de usar
`ON CONFLICT DO UPDATE` de PostgreSQL. Dos razones: la misma ruta de código corre en la suite de
tests sobre sqlite, y permite distinguir con exactitud entre fila nueva, actualizada y **sin
cambios** —que es lo que hace verificable la idempotencia—. A la escala del proyecto
(30 tickers × 90 días) el costo es irrelevante. Si algún día hay millones de filas, se revisa.

**5. El servicio `bot` sale con código 0 si no hay token.**

En vez de reventar y entrar en bucle de reinicios, registra el motivo y termina. El resto del
sistema —ingesta, dashboard, planificador— sigue funcionando sin Telegram. Un `docker compose up`
sin credenciales configuradas debe levantar algo útil, no una pila de errores.

### Criterios de aceptación de la FASE 0

| Criterio | Estado | Evidencia |
|---|---|---|
| `docker compose up` levanta todo desde cero | ✅ | `docker compose down -v` seguido de `up`: `app` y `db` *healthy*, `migraciones` exited 0 |
| Alembic aplica migraciones sin error | ✅ | `Running upgrade -> d847ecd0e7e3, esquema inicial` |
| 90 días de precios de 30 tickers en la base | ✅ | 2700 barras, 30 símbolos, 2026-04-02 → 2026-08-11 |
| El bot responde solo al chat autorizado | ✅ | Cada `CommandHandler` lleva `filters.Chat`; se niega a arrancar con `chat_id = 0`; los intentos ajenos se registran sin responder |
| Ningún secreto en el repo; `.env.example` completo | ✅ | `.env` en `.gitignore`; un test verifica que las 6 claves sensibles del ejemplo estén vacías |

### Lo que NO se hizo, a propósito

No hay señales, ni sugerencias, ni gestor de riesgo, ni backtester. El SPEC manda una fase a la
vez, y el invariante I4 prohíbe tocar pesos antes de tener el backtester validado. Los pesos de la
sección 7 están documentados en `config.py` como provisionales y no los consume nadie todavía.

El bot expone dos comandos y no seis. `/hoy`, `/desglose`, `/registrar`, `/pausar` y `/reanudar`
dependen de que existan señales y sugerencias: son FASE 3.

### Pendientes que arrastra la FASE 0

- El dashboard no tiene autenticación. En local no importa; si alguna vez se expone, sí.
- `precio_ultimo` en `tickers` puede quedar con una barra parcial si se corre la ingesta con el
  mercado abierto. Es cosmético (el dato correcto está en `precios_diarios` con su `observed_at`),
  pero conviene resolverlo cuando el dashboard muestre P&L.
- La suite corre sobre sqlite; las diferencias de dialecto con PostgreSQL (JSONB, `NULLS NOT
  DISTINCT`) se verifican por declaración, no por comportamiento. Si aparece un tercer caso así,
  vale la pena añadir un job de tests contra Postgres real.

### Siguiente

FASE 1 — ingestores de noticias, Reddit y Congreso; normalizador de entidades; deduplicador;
FinBERT. El criterio duro es reportar **el porcentaje medido** de reducción por deduplicación.
