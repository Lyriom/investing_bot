# investing_bot — Especificación técnica y prompt de construcción

> Este documento es a la vez el **prompt** para construir el sistema y la **fuente de verdad** del diseño.
> Guardarlo en el repo como `docs/SPEC.md`. Toda decisión de arquitectura se contrasta contra este archivo.

---

## 0. Cómo usar este documento

Pegar el contenido completo como contexto inicial en Claude Code (o el asistente que se use), seguido de una
instrucción concreta de fase, por ejemplo:

```
Lee la especificación completa. Implementa únicamente la FASE 0.
No adelantes trabajo de fases posteriores. Al terminar, lista los criterios
de aceptación de la FASE 0 y demuestra que cada uno se cumple.
```

Trabajar **una fase a la vez**. Cada fase tiene criterios de aceptación verificables.

---

## 1. Contexto y objetivo

Sistema de **generación de señales de inversión** para acciones y ETFs del mercado de EE.UU.,
que ingiere datos de múltiples fuentes independientes, los sintetiza en un score por ticker,
y entrega un digest diario auditable vía Telegram.

**El sistema NO ejecuta órdenes.** El operador humano ejecuta manualmente en Hapi
(bróker sin API pública). El sistema sugiere, explica y registra; la persona decide.

### Objetivo doble, en orden de prioridad

1. **Aprender y construir una pieza de portafolio seria**: pipeline de datos, NLP financiero,
   backtesting con disciplina point-in-time, y un dataset etiquetado propio.
2. **Operar capital real pequeño** (< USD 500) como mecanismo que fuerza honestidad en los resultados.

Si el backtesting no muestra ventaja, **el proyecto sigue siendo un éxito**: el objetivo 1 se cumplió
y se evitó perder dinero. El sistema debe estar diseñado para poder concluir "esto no funciona"
sin que eso se sienta como un fracaso.

### Perfil operativo

| Parámetro | Valor |
|---|---|
| Capital inicial | USD 100–150 (escalable hasta 500) |
| Horizonte de las señales | 3 a 20 días hábiles (swing) |
| Frecuencia de decisión | 1 vez al día, tras cierre de mercado |
| Ejecución | Manual, en Hapi, al día siguiente |
| Zona horaria del operador | UTC-5 fijo (Ecuador) |
| Mercado | 9:30–16:00 ET (= 8:30–15:00 local en horario de verano de EE.UU., 9:30–16:00 local en invierno) |

---

## 2. Invariantes no negociables

Estas reglas son la columna vertebral. Cualquier implementación que las viole está mal, sin discusión.

### I1 — Disciplina point-in-time

**Toda tabla de datos externos lleva dos timestamps distintos:**

- `event_at` — cuándo ocurrió el hecho en el mundo real
- `observed_at` — cuándo el sistema pudo conocerlo

El backtester **solo puede leer filas con `observed_at <= fecha_simulada`**. Nunca `event_at`.

Ejemplo del error que esta regla previene: un trade del Congreso ejecutado el 1 de marzo se hace público
el 14 de abril (la ley da 45 días). Un backtest que use el 1 de marzo como fecha de entrada mostrará
resultados espectaculares y completamente falsos. Este es el bug más común y más caro del dominio.

### I2 — El sistema jamás envía órdenes a ningún bróker

No hay código de ejecución. No hay credenciales de bróker. No hay scraping ni automatización de la app de Hapi
(violaría sus términos de servicio y pondría en riesgo la cuenta y los fondos). La única salida del sistema
es un mensaje de Telegram y filas en la base de datos.

### I3 — Toda sugerencia es auditable

Ninguna señal se emite sin: (a) el desglose numérico de cada componente que la formó,
(b) la versión del modelo/pesos que la generó, (c) el precio de invalidación (stop).
Debe ser posible reconstruir seis meses después *por qué* el sistema dijo lo que dijo.

### I4 — No se optimizan pesos antes de tener backtester

Los pesos iniciales son fijos y arbitrarios, documentados como tales. Optimizar parámetros sobre datos
con fuga de información produce sobreajuste disfrazado de descubrimiento.

### I5 — Los secretos nunca entran al repositorio

Token de Telegram, API keys, credenciales de base de datos: solo en `.env` local, ignorado por git,
cargado vía `pydantic-settings`. El repo incluye `.env.example` con las claves vacías.
Si un secreto se filtra alguna vez, se rota de inmediato — no se borra el commit y se hace como si nada.

---

## 3. Stack técnico

```
Lenguaje      Python 3.11+
API/Web       FastAPI + Jinja2 + HTMX
ORM           SQLAlchemy 2.0 (estilo declarativo moderno, tipado)
Migraciones   Alembic
BD            PostgreSQL 16
Scheduler     APScheduler (AsyncIOScheduler)
Bot           python-telegram-bot v21+ (async)
HTTP          httpx (async)
NLP           transformers + torch (FinBERT: ProsusAI/finbert)
Datos         pandas, numpy
Config        pydantic-settings
Logs          structlog (JSON estructurado)
Tests         pytest + pytest-asyncio
Contenedores  Docker Compose (postgres + app)
Calidad       ruff (lint + format), mypy
```

### Convenciones de código

- Nombres de variables, funciones, tablas y columnas **en español, sin tildes ni caracteres especiales**
  (`fecha_transaccion`, `calcular_score`, `precios_diarios`).
- Docstrings y comentarios en español.
- Nombres de librerías, clases de framework y APIs externas se dejan como son.
- Type hints obligatorios en toda función pública.
- Nada de `print()`: solo `structlog`.

---

## 4. Estructura del repositorio

```
investing_bot/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── .env.example
├── .gitignore
├── README.md
├── docs/
│   ├── SPEC.md                    # este documento
│   └── bitacora.md                # decisiones y resultados, en orden cronológico
├── alembic/
│   └── versions/
├── src/
│   └── investing_bot/
│       ├── __init__.py
│       ├── config.py              # Settings con pydantic-settings
│       ├── db.py                  # engine, sesion, Base
│       ├── modelos/               # modelos SQLAlchemy, uno por archivo
│       │   ├── ticker.py
│       │   ├── precio.py
│       │   ├── noticia.py
│       │   ├── reddit.py
│       │   ├── congreso.py
│       │   ├── senal.py
│       │   ├── sugerencia.py
│       │   └── ejecucion.py
│       ├── ingestores/
│       │   ├── base.py            # clase abstracta Ingestor
│       │   ├── precios.py
│       │   ├── noticias.py
│       │   ├── reddit.py
│       │   └── congreso.py
│       ├── normalizador/
│       │   ├── entidades.py       # texto -> ticker
│       │   └── deduplicador.py
│       ├── nlp/
│       │   └── sentimiento.py     # wrapper FinBERT
│       ├── senales/
│       │   ├── base.py            # clase abstracta Senal
│       │   ├── deriva_noticias.py
│       │   ├── velocidad_reddit.py
│       │   ├── consenso_congreso.py
│       │   ├── regimen.py
│       │   └── motor.py           # combina y pondera
│       ├── riesgo/
│       │   └── gestor.py
│       ├── telegram/
│       │   ├── bot.py
│       │   ├── handlers.py
│       │   └── formato.py         # construccion del digest
│       ├── backtest/
│       │   ├── motor.py
│       │   ├── portafolio.py
│       │   └── metricas.py
│       ├── web/
│       │   ├── app.py             # FastAPI
│       │   ├── rutas/
│       │   └── plantillas/
│       └── planificador.py        # APScheduler: registra todos los jobs
└── tests/
    ├── conftest.py
    ├── test_ingestores/
    ├── test_senales/
    ├── test_riesgo/
    └── test_backtest/
```

---

## 5. Modelo de datos

Todas las tablas llevan `id` (UUID o bigserial), `creado_at` y, donde aplique, `observed_at`.

### `tickers`
```
symbol PK, nombre, sector, industria, volumen_promedio_30d,
precio_ultimo, en_whitelist BOOL, activo BOOL, actualizado_at
```
`en_whitelist` es el filtro maestro: nada se sugiere fuera de la whitelist.

### `precios_diarios`
```
symbol FK, fecha, apertura, maximo, minimo, cierre, cierre_ajustado, volumen
UNIQUE(symbol, fecha)
```

### `noticias`
```
id, symbol FK NULL, titulo, resumen, url, fuente,
hash_contenido,           -- para deduplicacion
event_at, observed_at,
sentimiento NUMERIC,      -- [-1, 1]
confianza NUMERIC,        -- [0, 1]
modelo_usado TEXT,        -- "finbert-v1" | "claude-..." etc.
es_duplicado BOOL, id_original FK NULL
UNIQUE(hash_contenido)
```

### `reddit_diario`
```
symbol FK, fecha, subreddit, menciones INT,
sentimiento_promedio NUMERIC, upvotes_totales INT, observed_at
UNIQUE(symbol, fecha, subreddit)
```

### `congreso_trades`
```
id, miembro, camara, partido, estado,
symbol FK NULL, descripcion_activo, tipo,       -- compra | venta | intercambio
monto_min NUMERIC, monto_max NUMERIC,
fecha_transaccion DATE,   -- event_at
fecha_disclosure DATE,    -- cuando se publico
observed_at,              -- cuando lo ingerimos
dias_retraso INT, presentacion_tardia BOOL,
url_filing
UNIQUE(miembro, symbol, fecha_transaccion, monto_min, tipo)
```

### `senales`
```
id, symbol FK, fecha, score_total NUMERIC,
componentes JSONB,        -- {"deriva": 32, "reddit": 21, "congreso": 15, "regimen": -20}
regimen_mercado TEXT,     -- "alcista" | "riesgo"
version_modelo TEXT, creado_at
UNIQUE(symbol, fecha, version_modelo)
```

### `sugerencias`
```
id, senal_id FK, symbol FK, accion,          -- comprar | vender | mantener
precio_referencia NUMERIC, stop_sugerido NUMERIC,
tamano_sugerido_usd NUMERIC, razon TEXT,
enviada_at, mensaje_telegram_id
```

### `ejecuciones`
```
id, sugerencia_id FK NULL,    -- NULL si el operador actuo por cuenta propia
symbol FK, accion, precio_real NUMERIC, cantidad NUMERIC,
comisiones NUMERIC, fecha, notas
```

> `sugerencia_id` es nullable **a propósito**: permite registrar operaciones tomadas sin señal
> y comparar, con el tiempo, el criterio humano contra el del sistema. Ese contraste es uno de los
> datos más informativos que el proyecto va a producir.

### `posiciones_sombra`
```
id, symbol FK, fecha_entrada, precio_entrada, tamano_usd, stop,
fecha_salida NULL, precio_salida NULL, motivo_salida NULL, abierta BOOL
```
Portafolio simulado de la Fase 3, sin dinero real.

---

## 6. Componentes

### 6.1 Ingestores

Clase base `Ingestor` con contrato: `async def ejecutar() -> ResultadoIngesta`.
Requisitos comunes:

- **Idempotentes**: correr dos veces el mismo día no duplica filas (usar los UNIQUE + upsert).
- **Tolerantes a fallo**: si una fuente cae, se registra el error y las demás siguen.
  Nunca una excepción de un ingestor tumba el pipeline.
- **Rate-limit aware**: backoff exponencial con jitter, respetando los límites del free tier.
- Escriben siempre `observed_at = now()`.

| Ingestor | Fuente | Frecuencia |
|---|---|---|
| `precios` | `yfinance` (respaldo: Finnhub free) | Diaria, 17:00 ET |
| `noticias` | Finnhub free / Marketaux free | Cada 4 h |
| `reddit` | `praw` sobre r/wallstreetbets, r/stocks, r/investing | Cada 6 h |
| `congreso` | Datasets `house-stock-watcher` / `senate-stock-watcher` (JSON público) | Diaria |

### 6.2 Normalizador

**Resolución de entidades** (`entidades.py`): mapear texto libre a ticker.
Estrategia en cascada: coincidencia exacta de símbolo con `$` → nombre de empresa exacto →
alias conocidos (diccionario curado) → descarte. **Preferir falsos negativos a falsos positivos**:
es mucho peor asignar una noticia al ticker equivocado que perderla.

**Deduplicación** (`deduplicador.py`): hash normalizado del título (minúsculas, sin puntuación,
sin stopwords) + ventana de 48 h. Diez portales replicando el mismo cable de agencia son
**un dato, no diez**. Sin esto, el score se infla por popularidad mediática y no por señal.

### 6.3 Motor de señales

Cada señal implementa: `async def calcular(symbol, fecha) -> ComponenteSenal`
devolviendo `{valor: float, detalle: dict, datos_suficientes: bool}`.

Regla crítica: **cada señal solo puede consultar filas con `observed_at <= fecha`**.
Esto se aplica en una capa de repositorio compartida, no en cada señal por separado,
para que sea imposible saltárselo por descuido.

Si `datos_suficientes` es falso, el componente aporta 0 — nunca se inventa un valor.

### 6.4 Gestor de riesgo

Filtro final. Puede **vetar** cualquier sugerencia. Reglas (todas configurables en `config.py`):

```
MAX_POSICIONES_ABIERTAS      = 5
MIN_TAMANO_POSICION_USD      = 50
MAX_PCT_POR_POSICION         = 0.25      # del capital total
MAX_POSICIONES_POR_SECTOR    = 2
STOP_LOSS_PCT                = 0.08
MAX_PERDIDA_MENSUAL_PCT      = 0.10      # alcanzado -> modo defensivo
MIN_VOLUMEN_DIARIO           = 1_000_000
MIN_PRECIO_ACCION            = 5.00
MAX_SUGERENCIAS_POR_DIA      = 2
DIAS_COOLDOWN_MISMO_TICKER   = 5
```

**Modo defensivo**: no se generan sugerencias de compra; solo de venta o mantener.
Se activa por régimen de mercado bajista o por pérdida mensual sobre el umbral.
El digest debe decir explícitamente que está en modo defensivo y por qué.

**Costo de fricción**: el gestor debe descartar sugerencias donde el costo estimado
(clearing ~USD 0.15 por operación fraccionada, ida y vuelta, más spread estimado)
supere el 1 % del tamaño de la posición. Con capital pequeño, la fricción mata estrategias
que en el papel se veían rentables.

### 6.5 Bot de Telegram

Comandos:

| Comando | Función |
|---|---|
| `/start` | Vincula el chat_id autorizado |
| `/estado` | Portafolio actual, P&L, régimen de mercado, modo activo |
| `/hoy` | Reenvía el digest del día |
| `/desglose <symbol>` | Componentes detallados del score |
| `/registrar` | Flujo conversacional para grabar una ejecución real |
| `/pausar` `/reanudar` | Kill switch del envío de sugerencias |

**Seguridad**: solo responde a un `chat_id` en la whitelist (`TELEGRAM_CHAT_ID_AUTORIZADO`).
Cualquier otro chat recibe silencio, no un mensaje de error.

Formato del digest diario:

```
📊 11 ago 2026 — Régimen: RIESGO (SPY < MA200)
Modo defensivo: sin compras nuevas.

⚠️ Vigilar — NVDA · score 68/100
   ├ Deriva post-noticia   +32   sorpresa +8% hace 4d
   ├ Velocidad Reddit      +21   z=2.4, sent +0.6
   ├ Consenso Congreso     +15   3 miembros, neto compra
   └ Régimen               -20   veto parcial

   Entrada ref  $178.40
   Stop         $169.00  (-5.3%)
   Tamaño       $95      (19% del portafolio)
   Costo est.   $0.30    (0.32%)

   /desglose NVDA   ·   /registrar
```

### 6.6 Backtester

El componente más importante del proyecto.

- Simulación día a día, leyendo **exclusivamente** por `observed_at`.
- Parámetro obligatorio **`retraso_entrada_dias`** (0, 1, 3): simula que el operador humano
  ejecuta con demora. **Una señal cuyo retorno se desploma con 1 día de retraso queda descartada**,
  porque no es ejecutable en este esquema operativo.
- Modelo de costos realista: clearing fijo por operación, spread estimado, y slippage.
- Métricas: retorno total, CAGR, Sharpe, máximo drawdown, win rate, factor de beneficio,
  número de operaciones, retorno neto de costos.
- **Benchmark obligatorio contra comprar y mantener SPY.** Si la estrategia no supera
  a comprar el índice y quedarse quieto, no tiene razón de existir; el reporte debe decirlo
  con esas palabras.
- Salida: reporte reproducible con semilla fija, guardado en `docs/bitacora.md`.

### 6.7 Dashboard

FastAPI + Jinja2 + HTMX. Vistas: estado del pipeline (última corrida de cada ingestor,
filas ingeridas, errores), scores actuales, historial de sugerencias con su resultado,
portafolio sombra, y reportes de backtest. Sin JavaScript de framework; HTMX y nada más.

---

## 7. Definición de las señales

Pesos iniciales **fijos y arbitrarios** (ver invariante I4). Documentados como provisionales.

### S1 — Deriva post-noticia · peso 0.40 · horizonte 3–15 días

Anomalía documentada: tras una sorpresa fuerte en resultados, el precio tiende a seguir derivando
en la dirección de la sorpresa durante días. Es la única de las cuatro con respaldo académico sólido.

Se clasifica el evento con FinBERT. La señal **se activa en los días posteriores, nunca en el minuto
del titular** — competir por velocidad contra firmas colocadas en el datacenter del NASDAQ,
desde una conexión doméstica en Quito, no es una estrategia.

### S2 — Velocidad de menciones en Reddit · peso 0.25 · horizonte 2–10 días

Z-score de las menciones diarias contra la media móvil de 30 días, cruzado con el sentimiento promedio.
Filtros obligatorios: volumen > 1 M y precio > USD 5.

Riesgo explícito: en un pump de foro, el que llega tarde **es la liquidez de salida** del que entró antes.
Por eso el peso está limitado y el gestor de riesgo aplica cooldown.

### S3 — Consenso del Congreso · peso 0.15 · horizonte meses

**No es** "un legislador compró X, entonces comprar X". Es: cuántos miembros *distintos* compraron
el ticker o su sector en 90 días, neto de ventas, ponderado por monto y **penalizado por antigüedad
del disclosure**.

La ley concede 45 días para reportar. Cuando el dato es público, el trade ocurrió hace hasta seis semanas
y ya lo procesaron miles de sistemas. **Esto no es información privilegiada, es arqueología.**
Sirve como sesgo sectorial de fondo y watchlist, no como gatillo.

### S4 — Régimen de mercado · veto, no señal

Si SPY cierra bajo su media móvil de 200 días → modo defensivo, sin compras nuevas.
Regla simple y de las más valiosas que existen: sin ella, cualquier estrategia solo-largos
se ve excelente en backtest y se destruye en el primer mercado bajista.

**Score final** = suma ponderada de S1–S3, con S4 aplicado como multiplicador o veto.
Normalizado a 0–100. Umbral de sugerencia: 60.

---

## 8. Fases y criterios de aceptación

### FASE 0 — Cimientos
Docker Compose, esquema completo, Alembic, `config.py`, ingestor de precios,
bot de Telegram respondiendo `/start` y `/estado`, dashboard mínimo.

✅ `docker compose up` levanta todo desde cero
✅ Alembic aplica migraciones sin error
✅ 90 días de precios de 30 tickers en la base
✅ El bot responde solo al chat autorizado
✅ Ningún secreto en el repo; `.env.example` completo

### FASE 1 — Datos
Ingestores de Reddit, Congreso y noticias. Normalizador y deduplicador. FinBERT.

✅ Los cuatro ingestores corren en el scheduler sin intervención
✅ La deduplicación reduce el volumen de noticias de forma medible (reportar el %)
✅ Tests: la resolución de entidades no produce falsos positivos en un set curado
✅ Todas las filas tienen `observed_at` correcto

### FASE 2 — Backtester
Motor de simulación, portafolio, métricas, `retraso_entrada_dias`.

✅ **Test que demuestra que el backtester no puede leer datos futuros** (el test más importante del repo)
✅ Reporte comparativo con retraso 0 / 1 / 3 días
✅ Benchmark contra SPY buy-and-hold
✅ Resultados reproducibles con semilla fija

### FASE 3 — Señales y digest
Las cuatro señales, el motor de combinación, el gestor de riesgo, el digest de Telegram.

✅ Digest diario automático a las 18:15 ET
✅ Toda sugerencia trae desglose, stop y tamaño
✅ El modo defensivo se activa correctamente (probarlo con datos históricos de un mercado bajista)
✅ `/registrar` graba ejecuciones

### FASE 4 — Portafolio sombra (mínimo 3 meses, sin dinero)
El sistema sugiere, el operador registra precios reales de referencia, nadie invierte nada.

✅ ~60 sugerencias con resultado medido
✅ Comparación: sugerencias del bot vs SPY vs decisiones humanas sin señal
✅ Slippage real medido contra el precio de referencia
✅ **Decisión documentada y honesta**: seguir a dinero real, o no

### FASE 5 — Capital real (solo si la Fase 4 lo justifica)
Empezar con USD 100–150, no con los 500. Nada de código nuevo de ejecución (invariante I2).

---

## 9. Anti-objetivos

Cosas que el sistema **no** debe hacer, aunque parezcan buenas ideas:

- ❌ Ejecutar órdenes automáticamente en cualquier bróker
- ❌ Scrapear o automatizar la app de Hapi
- ❌ Operar intradía o buscar ventaja por velocidad
- ❌ Optimizar pesos antes de tener el backtester validado
- ❌ Usar apalancamiento, margen, opciones o cripto
- ❌ Añadir fuentes de datos correlacionadas y contarlas como independientes
- ❌ Mostrar resultados de backtest sin costos de transacción
- ❌ Ocultar o suavizar resultados negativos en la bitácora

---

## 10. Testing

- Cobertura mínima: 70 % en `senales/`, `riesgo/` y `backtest/`.
- **Test crítico**: intentar que el backtester lea una fila con `observed_at` futuro debe fallar
  ruidosamente. Si ese test no existe, el proyecto no es confiable.
- Fixtures con datos sintéticos deterministas; nada de golpear APIs externas en tests.
- Tests de idempotencia: correr cada ingestor dos veces no debe duplicar filas.

---

## 11. Bitácora

`docs/bitacora.md` se actualiza en cada hito con: fecha, qué se construyó, qué se midió,
qué resultó y qué se decidió. **Los resultados negativos se registran igual que los positivos.**
Este archivo es, para efectos de portafolio y de postulaciones académicas, tan valioso como el código.
