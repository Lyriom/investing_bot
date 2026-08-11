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

---

## 2026-08-11 — Empaquetado para servidor

### Qué se construyó

Preparación del despliegue. No toca la lógica del sistema: solo cómo se empaqueta y se corre.

- `Dockerfile` multi-stage con destinos `desarrollo` y `produccion`. El toolchain de
  compilación vive en un stage intermedio y no llega a la imagen final.
- `.dockerignore`, que no existía.
- `docker-compose.produccion.yml`, separado del de desarrollo.
- Apagado ordenado del planificador ante SIGTERM.
- `docs/despliegue.md` con el procedimiento completo.

### Qué se midió

| Medición | Antes | Después |
|---|---|---|
| Contexto de build enviado al daemon | 363 MB | **7,6 kB** |
| Tamaño de la imagen de servidor | 810 MB | **657 MB** |
| Tiempo de `docker stop planificador` | 10 s (SIGKILL) | **0 s** |
| Tests | 77 pasan | 77 pasan (75 + 2 saltados dentro del contenedor) |

Verificado sobre la pila de producción real: migraciones aplicadas, whitelist sembrada,
2700 barras ingeridas desde la imagen `produccion`, dashboard respondiendo por loopback.

### Qué se decidió

**1. Faltaba `.dockerignore`, y era un problema de seguridad además de lentitud.**

Sin él, el `.venv` local (319 MB) viajaba al daemon en cada build. Peor: nada impedía que
un `.env` con secretos acabara dentro de una imagen que después se sube a un registro.
El invariante I5 protege el repositorio, pero una imagen publicada filtra igual de bien.

**2. `produccion` es el último stage, y por tanto el destino por defecto.**

`docker build .` sin `--target` devuelve la imagen de servidor. Que el descuido lleve a la
imagen más segura y no a la que trae pytest y el código fuente suelto.

**3. El dashboard se ata a `127.0.0.1` en producción.**

No tiene autenticación y, a partir de la FASE 3, mostrará señales y portafolio. La
alternativa —publicarlo y "ya le pondré auth después"— es exactamente cómo se filtran estos
paneles. El acceso pasa por proxy inverso con TLS y contraseña, o por túnel SSH. La base de
datos directamente no publica puerto.

**4. `${VARIABLE:?mensaje}` en vez de valores por defecto.**

El compose de desarrollo tiene defaults cómodos (`investing_local` de contraseña). El de
producción falla al arrancar si falta un secreto. Un default cómodo que sobrevive hasta el
servidor es una contraseña de base conocida por cualquiera que lea el repo.

**5. El planificador atiende SIGTERM, y apaga con `wait=True`.**

Antes ignoraba la señal: Docker esperaba 10 s y recurría a SIGKILL en cada despliegue,
matando el proceso con el pool de conexiones a medio cerrar. Ahora termina en 0 s. El
`wait=True` es deliberado: si hay una ingesta a mitad de camino se la deja terminar, porque
perder una corrida por un despliegue deja un hueco silencioso en los datos —y en un sistema
point-in-time un hueco no se puede rellenar después.

**6. Dos tests pasaron a saltarse dentro del contenedor.**

`test_env_esta_ignorado_por_git` y `test_env_example_no_trae_valores_reales` verifican
higiene del *repositorio*, no comportamiento de la *aplicación*, y los archivos que leen no
están —a propósito— en la imagen. Se saltan con motivo explícito en lugar de meter
`.gitignore` en una imagen de Docker para que un test verde. Siguen corriendo en local y en
cualquier CI que parta de un checkout.

### Pendiente

- No hay CI. Hoy los tests corren porque alguien se acuerda. Un workflow de GitHub Actions
  que ejecute `ruff`, `mypy` y `pytest` en cada push es lo siguiente en esta línea.
- No hay registro de imágenes: el servidor construye con `--build`. Suficiente para una
  máquina; si algún día hay dos, hay que publicar la imagen en GHCR.
- El respaldo de la base está documentado como cron en `docs/despliegue.md`, pero no
  verificado. Un respaldo que nunca se restauró no es un respaldo.

---

## 2026-08-11 — Arranque en plataformas sin `depends_on` (Easypanel)

### Qué se construyó

El despliegue real se hace en Easypanel, donde cada servicio es un contenedor suelto y no
existe `depends_on`. El repo asumía lo contrario —un contenedor `migraciones` que corre y
termina antes que los demás— así que tal cual no arrancaba ahí.

- `docker/arrancar.sh` como entrypoint de la imagen, controlado por variables:
  `ESPERAR_BD`, `ESPERAR_BD_TIMEOUT`, `EJECUTAR_MIGRACIONES` y `SERVICIO`.
- `investing-bot esperar-bd`, que bloquea hasta que PostgreSQL acepte consultas.
- Reescritura automática de `postgres://` y `postgresql://` a `postgresql+asyncpg://`.
- `docs/easypanel.md` con el procedimiento de los cuatro servicios.

### Qué se midió

Simulación del escenario exacto: contenedores sueltos en una red común, sin `depends_on`,
levantando **la aplicación antes que la base** a propósito.

| Comprobación | Resultado |
|---|---|
| App arranca sin base disponible | `esperando_bd` ×3, sin reventar |
| Base aparece 20 s después | `bd_disponible intentos=4` |
| Migraciones y siembra automáticas | `upgrade -> d847ecd0e7e3`, `whitelist_sembrada nuevas=30` |
| Dashboard tras la secuencia | `Uvicorn running`, `/salud` → `{"estado":"ok"}` |
| Ingesta desde otro contenedor | 2700 barras, 0 errores |
| `SERVICIO=planificador` | levanta APScheduler, ignora el CMD `api` de la imagen |
| Base inalcanzable | falla con código 1 tras agotar el plazo, no cuelga |
| Tests | 81 pasan |

### Qué se decidió

**1. Variables de entorno en vez de sobreescribir el comando.**

Los tres procesos (`api`, `bot`, `planificador`) salen de la misma imagen. Se podría
distinguirlos con un *command override* en cada servicio, pero en estos paneles cambiar una
variable es más cómodo y menos frágil que un campo de comando que cada versión de la UI
coloca en otro sitio. `SERVICIO` manda sobre el `CMD`; si no está definida, el `CMD` se
respeta y Docker Compose sigue funcionando exactamente igual que antes.

**2. `ESPERAR_BD` por defecto en `false`.**

Tentador ponerlo en `true` para que "simplemente funcione". Se descartó: haría que cualquier
comando puntual (`investing-bot --version`, entrar a la terminal del contenedor) se quedara
esperando hasta 90 s una base que quizá ni se necesita. Lo activa quien lo necesita, y el
bloque de variables de `docs/easypanel.md` ya lo trae puesto.

**3. La URL de la base se reescribe sola.**

Easypanel, Railway y Heroku entregan la cadena como `postgres://`. SQLAlchemy resuelve ese
esquema a psycopg2, un driver síncrono que el proyecto no instala, y el arranque falla con
un error que no explica nada. Pegar la URL tal cual como la muestra el panel tiene que
funcionar; obligar a editarla a mano es una trampa que se cobra la primera vez y cada vez
que se rota la contraseña. La reescritura respeta contraseñas que contengan la subcadena
`postgres`, y hay un test que lo fija.

**4. Las migraciones las aplica un solo servicio, el del dashboard.**

Alembic toma un lock y `sembrar` es idempotente, así que hacerlo en los tres no corrompería
nada — pero tampoco aporta. `EJECUTAR_MIGRACIONES=true` va solo en `investing`.

### Pendiente

- El dashboard sigue sin autenticación propia. En Easypanel se resuelve con el Basic Auth de
  la pestaña Security, que es una capa del proxy, no de la aplicación. Si alguna vez se
  expone fuera de ese proxy, hace falta auth de verdad.
- No hay despliegue automático por push. Easypanel puede hacerlo con un webhook de GitHub;
  no está configurado.

---

## 2026-08-11 — FASES 1 y 3: datos, señales y alertas

### Contexto de la decisión

El operador pidió las alertas dos veces, sabiendo que el SPEC ordena hacer la FASE 2
(backtester) antes que la FASE 3. Se construyeron igual, con dos salvaguardas: el digest
lleva una advertencia fija de que las señales no pasaron por ningún backtest, y todo
entra al portafolio sombra, sin dinero real. **Esto no sustituye a la FASE 2.**

### Qué se construyó

**FASE 1 — datos**

- Ingestores de noticias (Finnhub), Reddit (praw) y Congreso (stock-watcher).
- Resolución de entidades en cascada: cashtag → alias curado → nombre de empresa →
  símbolo suelto no ambiguo.
- Deduplicación en dos niveles: hash del titular normalizado + similitud de Jaccard,
  ambos en ventana de 48 h.
- Clasificación de sentimiento con FinBERT y respaldo léxico.

**FASE 3 — señales y digest**

- `datos/repositorio_pit.py`: **toda** lectura que alimenta una señal pasa por aquí y
  filtra por `observed_at`. Es la pieza que hace cumplir el invariante I1.
- Las cuatro señales, el motor de combinación, el gestor de riesgo con 11 reglas de veto.
- Digest diario a las 18:15 ET; comandos `/hoy`, `/desglose`, `/registrar`, `/pausar`,
  `/reanudar`.

### Qué se midió

| Medición | Resultado |
|---|---|
| Tests | **215 pasan** (antes 81) |
| Cobertura de `datos/` | **100 %** |
| Cobertura de `senales/` | 87–100 % por módulo |
| Cobertura de `riesgo/` | 93 % |
| Reducción por deduplicación (set sintético) | **70 %** |
| Falsos positivos de entidades sobre 10 casos trampa | **0** |
| Régimen calculado sobre 400 días reales | SPY **+9,4 %** sobre su MA200 → alcista |
| Barras en la base | 12 000 (30 tickers × 400 días) |

### Hallazgos

**1. Los parámetros por defecto del SPEC son mutuamente incompatibles.**

`MAX_PCT_POR_POSICION = 0.25` y `MIN_TAMANO_POSICION_USD = 50` implican un capital mínimo
de **200 USD**. Con los 100–150 que el SPEC propone para empezar, la posición máxima serían
37,50 USD, por debajo del mínimo viable, y **el sistema no emitiría jamás una sugerencia**.

El código lo detecta y veta con un mensaje explícito, en vez de inflar la posición hasta el
mínimo — que era lo que hacía la primera versión y rompía el límite de concentración que
`max_pct_por_posicion` existe para imponer. Un test lo fija.

**2. La fuente del Congreso está caída.**

Los datasets de house-stock-watcher y senate-stock-watcher devuelven **403 Forbidden**. Se
verificó contra las dos URLs; solo responde el sitio oficial del Clerk de la Cámara, que
sirve PDFs y requiere un parser entero. El ingestor está construido y probado con 14 tests
sobre datos sintéticos; lo que falta es una fuente viva, no código. Las URLs pasaron a ser
configurables para poder apuntar a un espejo sin tocar el código.

Lo que sí quedó demostrado en producción: la tolerancia a fallo del SPEC 6.1 funciona. Las
dos cámaras fallaron, se registró el error en `corridas_ingesta`, y ni el pipeline ni los
demás ingestores se enteraron.

**3. Los dígitos no se pueden descartar como ruido al deduplicar.**

Un test destapó que `tokens_significativos` eliminaba los tokens de un carácter, de modo que
"recalls 2 million" y "recalls 3 million" producían el mismo hash y una de las dos se perdía
como duplicado. En un titular financiero la cifra suele ser la noticia.

### Decisiones

**El peso de una señal sin datos no se reparte entre las demás.** Se divide siempre por el
peso total (0.80), no por el de las señales con datos. Si se repartiera, dos señales podrían
fingir la convicción de tres, y un ticker con una sola fuente parecería tan sólido como uno
con las tres.

**50 es el score neutro, no 0.** Ausencia de evidencia no es evidencia en contra.

**El régimen solo puede recortar un score, nunca subirlo.** Y si no hay 200 barras para
medirlo, el régimen es `desconocido` y **eso también activa el modo defensivo**: no saber en
qué mercado estamos no es razón para comprar.

**FinBERT es un extra opcional.** torch pesa ~2 GB y triplicaría la imagen de servidor. Sin
el extra `nlp` se clasifica con un léxico mucho más pobre — y cada fila guarda en
`modelo_usado` con cuál se clasificó, para que al analizar resultados nunca se confunda una
salida de FinBERT con una del léxico.

**La señal de deriva ignora las noticias de las últimas 24 h.** Competir por velocidad contra
firmas colocadas en el datacenter del NASDAQ, desde una conexión doméstica en Quito, no es
una estrategia.

### Estado real del sistema

Con las claves de API vacías, el digest sale **sin sugerencias** y lo dice: *"Ninguna señal
tuvo datos suficientes"*. Es correcto, no un fallo. Para que produzca algo hacen falta:

- `FINNHUB_API_KEY` → habilita S1 (peso 0.40)
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` → habilita S2 (peso 0.25)
- Una fuente viva del Congreso → habilitaría S3 (peso 0.15)

Solo con S1 el score máximo alcanzable es 75/100, por encima del umbral de 60. Con S1+S2 el
sistema ya puede sugerir con dos fuentes independientes.

### Pendiente, y es lo importante

**La FASE 2 sigue sin hacerse.** Estas señales no han demostrado nada: los pesos son
arbitrarios (I4), nadie ha medido si superan a comprar SPY y quedarse quieto, y nadie ha
comprobado qué pasa con `retraso_entrada_dias = 1`. Hasta que el backtester exista y diga
algo, el digest es un ejercicio de ingeniería, no una recomendación de inversión — y el
propio mensaje lo dice en cada envío.
