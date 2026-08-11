# Despliegue en Easypanel

Easypanel no usa `docker-compose.yml` para los servicios de tipo **App**: cada
servicio es **un contenedor**, y no existe `depends_on`. Hay dos formas de
montarlo.

## Dos servicios (recomendado para un operador)

| Servicio | Tipo | Qué hace | ¿Dominio? |
|---|---|---|---|
| `investing-bbd` | Postgres | La base de datos | No |
| `investing` | App | **Todo**: dashboard, planificador y bot, en un proceso | Sí, puerto 8000 |

Se activa con `SERVICIO=todo`. Es lo que documenta el resto de esta guía.

## Cuatro servicios (separados)

| Servicio | Tipo | Qué hace | ¿Dominio? |
|---|---|---|---|
| `investing-bbd` | Postgres | La base de datos | No |
| `investing` | App | `SERVICIO=api` — dashboard | Sí, puerto 8000 |
| `planificador` | App | `SERVICIO=planificador` — ingesta + digest de las 18:15 ET | No |
| `bot` | App | `SERVICIO=bot` — responde `/hoy`, `/desglose`, `/pausar` | No |

Los tres App usan la misma imagen y el mismo repo; lo único que cambia entre
ellos es `SERVICIO`. Ver el [apéndice](#apéndice-el-despliegue-separado).

## Cuál elegir

**`todo` junta los tres en un proceso supervisado.** A cambio de configurar una
sola vez las variables, se pierde el aislamiento: si uno cae, el proceso termina
con código distinto de cero y Easypanel lo reinicia entero. Un token de Telegram
mal escrito tumba también el dashboard, con este mensaje en los logs:

```
servicio_caido  servicio=bot  error=The token `...` was rejected by the server.
```

Para un sistema de una persona, un reinicio de treinta segundos sale más barato
que mantener tres configuraciones en sincronía. Si necesitas que el dashboard
siga en pie mientras depuras el bot, separa los servicios.

Lo que **no** cambia entre las dos formas: el digest, la ingesta y el bot corren
igual. `todo` no es un modo degradado.

---

## 1. Crear la base de datos

En el proyecto (`thelonec`), **+ Service → Postgres**.

- **Name**: `investing-bbd`
- Deja que genere la contraseña.
- **Create** y luego **Deploy**.

Cuando termine, entra al servicio y copia de su pantalla:

- la **contraseña**
- el **host interno**, que en Easypanel tiene la forma `<proyecto>_<servicio>`;
  con este ejemplo sería `thelonec_investing-bbd`

> Copia el host y la contraseña de la pantalla del servicio, no de aquí. Si tu
> versión de Easypanel muestra una **Connection URL** completa, mejor: se puede
> pegar tal cual, incluso en formato `postgres://` — el proyecto reescribe el
> driver solo.

No le pongas dominio. Solo la usan los otros servicios del proyecto.

Activa los **backups** en este servicio. La base guarda el histórico
point-in-time, que no se puede reconstruir después: nadie te vende los
`observed_at` de ayer.

---

## 2. Conseguir las claves

Cuatro cosas, antes de tocar el resto. Sin las dos primeras el bot no existe;
sin las dos últimas el sistema arranca pero **el digest sale vacío todos los
días**, porque ninguna señal tiene datos.

| Dónde | Qué sacas | Para qué |
|---|---|---|
| **@BotFather** en Telegram → `/newbot` | `TELEGRAM_BOT_TOKEN` | El bot |
| **@userinfobot** en Telegram → le escribes cualquier cosa | `TELEGRAM_CHAT_ID_AUTORIZADO` | Tu número de chat |
| [finnhub.io/register](https://finnhub.io/register) | `FINNHUB_API_KEY` | **S1**, peso 0.40 |
| [marketaux.com/register](https://www.marketaux.com/register) | `MARKETAUX_API_KEY` | Respaldo de S1 |
| [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) | `REDDIT_CLIENT_ID` y `REDDIT_CLIENT_SECRET` | **S2**, peso 0.25 |

Las cinco son gratis. Finnhub es la única imprescindible.

### Marketaux: respaldo, no segunda opinión

Se consulta **solo** para los tickers donde Finnhub no trajo nada o falló.
Alimenta la misma señal S1: dos agregadores de titulares cubren en buena medida
las mismas fuentes y **no son independientes entre sí**, así que no suma peso —
suma cobertura y resistencia a que Finnhub se caiga.

El plan gratuito da **100 peticiones al día y 3 artículos por petición**. El
sistema se limita solo a 90 (`MAX_PETICIONES_MARKETAUX_DIA`) para que una caída
larga de Finnhub no agote la cuota en la primera corrida de la mañana. Cuando se
acaba, lo dice y sigue: `presupuesto_agotado proveedor=marketaux`.

Su `sentiment_score` propio se descarta: el sistema clasifica el sentimiento con
su propio modelo y anota cuál usó en `modelo_usado`. Mezclar dos escalas bajo la
misma columna haría incomparables las filas.

### Reddit: la app ya no basta

**El tipo tiene que ser `script`**, no `web app` — es el único que autentica con
la cuenta propia sin flujo OAuth. El `redirect uri` es obligatorio en el
formulario aunque `script` no lo use nunca: pon `http://localhost:8080`.

Una vez creada, el `client_id` es la cadena de **debajo del nombre de la app**,
sin etiqueta; el `secret` sí va etiquetado.

Pero desde junio de 2026, **crear la app no da acceso a la API**. Hay que
solicitarlo aparte (el enlace *register to use the API* del propio formulario) y
esperar aprobación bajo la Responsible Builder Policy: colas de 2 a 4 semanas, y
los proyectos personales son la categoría con más rechazos. Describe el uso con
precisión —cuántas lecturas, qué subreddits, sin publicar, sin fin comercial—;
las descripciones vagas son las que caen.

Si no llega la aprobación, el sistema funciona igual sin S2. Lee la sección
[Con una sola fuente](#con-una-sola-fuente-lo-que-cambia).

### Congreso

**No tiene fuente viva hoy**: los datasets de stock-watcher devuelven 403 desde
agosto de 2026. No hay clave que conseguir. S3 (peso 0.15) queda inactiva.

### Con una sola fuente: lo que cambia

Con S2 y S3 caídas queda solo S1. El sistema sigue funcionando —el score máximo
alcanzable es 75/100, por encima del umbral de 60— pero cada sugerencia vendría
de **un solo titular con confirmación de precio**, no del cruce de fuentes
independientes que es la premisa del SPEC. El gestor de riesgo solo exige hoy
que *alguna* señal tenga datos (`sin_evidencia`).

Es un sistema que funciona con menos evidencia de la que su diseño supone. Está
anotado en la bitácora y conviene decidirlo a conciencia, no por omisión.

---

## 3. Configurar el servicio `investing`

### Source

- **Owner / Repository**: `Lyriom` / `investing_bot`
- **Branch**: `main`
- **Build path**: `/`

### Build

Elige **Dockerfile** (no Nixpacks) y deja el path en `Dockerfile`.

No hace falta indicar ningún *target*: `produccion` es el último stage del
Dockerfile, así que es el que se construye por defecto. La imagen resultante no
lleva tests ni herramientas de desarrollo.

### Environment

Este es el bloque completo. Pégalo entero y sustituye lo que está en mayúsculas.

```env
ENTORNO=produccion
NIVEL_LOG=INFO

URL_BD=postgres://postgres:LA_CLAVE_DEL_PANEL@thelonec_investing-bbd:5432/postgres

ESPERAR_BD=true
ESPERAR_BD_TIMEOUT=120
EJECUTAR_MIGRACIONES=true
SERVICIO=todo

TELEGRAM_BOT_TOKEN=TU_TOKEN_DE_BOTFATHER
TELEGRAM_CHAT_ID_AUTORIZADO=TU_CHAT_ID

FINNHUB_API_KEY=TU_CLAVE_FINNHUB
MARKETAUX_API_KEY=TU_CLAVE_MARKETAUX
REDDIT_CLIENT_ID=TU_CLIENT_ID
REDDIT_CLIENT_SECRET=TU_CLIENT_SECRET
REDDIT_USER_AGENT=investing_bot/0.1 por u/TU_USUARIO

CAPITAL_TOTAL_USD=500
DIAS_HISTORIAL_PRECIOS_INICIAL=400
ZONA_HORARIA_MERCADO=America/New_York
ZONA_HORARIA_OPERADOR=America/Guayaquil
```

Ajusta `URL_BD` con el usuario, la contraseña, el host y el nombre de base que
muestre tu servicio de Postgres — Easypanel suele crear la base y el usuario
como `postgres`, pero confírmalo en su pantalla.

`SERVICIO=todo` es lo que arranca los tres a la vez. Con `EJECUTAR_MIGRACIONES=true`
el contenedor espera a que la base responda, aplica Alembic, siembra los 30
instrumentos y recién entonces levanta dashboard, planificador y bot.

Si dejas Telegram vacío, el sistema arranca igual sin bot y lo dice en los logs
(`bot_no_configurado`). Si pones un token **equivocado**, en cambio, el proceso
entero se cae y se reinicia en bucle: es el precio de tenerlo todo junto.

Sobre `CAPITAL_TOTAL_USD`: el mínimo viable es **200**. Con menos, el tamaño
máximo por posición (25 %) cae por debajo del mínimo por operación (50 USD) y el
gestor de riesgo veta absolutamente todo. Está documentado en la bitácora.

`DIAS_HISTORIAL_PRECIOS_INICIAL=400` no es capricho: la señal de régimen compara
SPY contra su media de 200 días. Con 90 días no hay 200 barras, el régimen sale
`desconocido`, el sistema entra en modo defensivo y no sugiere nada.

### Domains

- **Add domain** con tu dominio (o el `*.easypanel.host` que ofrece el panel).
- **Port: 8000** — no 80. El contenedor escucha en 8000 y el panel propone 80
  por defecto; si lo dejas así el dominio devuelve 502.
- Activa **HTTPS**.

### Security ⚠️

**El dashboard no tiene autenticación.** Muestra el estado del pipeline, las
señales y el portafolio sombra. En la pestaña **Security** activa **Basic Auth**
con usuario y contraseña antes de exponer el dominio. Sin eso, cualquiera que dé
con la URL ve tus datos.

### Deploy

Botón **Deploy**. En los logs deberías ver, en este orden:

```
esperando_bd
bd_disponible
Running upgrade -> d847ecd0e7e3, esquema inicial
whitelist_sembrada  nuevas=30 total=30
todo_en_uno_iniciado  servicios=['bot', 'planificador', 'api']
job_registrado  id=digest_diario  proxima_ejecucion=2026-08-11 18:15:00-04:00
bot_iniciado  chat_autorizado=...
Uvicorn running on http://0.0.0.0:8000
```

**Las dos líneas que importan** son `todo_en_uno_iniciado` con los tres
servicios, y `job_registrado id=digest_diario`. Si esa segunda no aparece, no va
a salir ningún digest.

Los cinco jobs que quedan programados:

| Job | Cuándo |
|---|---|
| Ingesta de precios | 17:00 ET, lunes a viernes |
| Ingesta de noticias | cada 4 h |
| Ingesta de Reddit | cada 6 h |
| Ingesta del Congreso | 06:30 ET |
| **Digest diario** | **18:15 ET, lunes a viernes** (17:15 en Ecuador) |

Y los comandos del bot:

| Comando | Qué hace |
|---|---|
| `/start` | Vincula el chat |
| `/hoy` | Reenvía el digest del día |
| `/desglose SYMBOL` | El detalle numérico de cada componente del score |
| `/registrar` | Anota una operación que ejecutaste a mano |
| `/pausar` / `/reanudar` | Kill switch: deja de enviar (sigue calculando) |

El bot **solo atiende el `chat_id` de la whitelist**. Cualquier otro chat recibe
silencio, no un mensaje de error. Con `TELEGRAM_CHAT_ID_AUTORIZADO=0` se niega a
arrancar, en vez de atender a cualquiera.

---

## 4. Cargar el historial y probar

El planificador solo corre a sus horas. Para no esperar, abre la **terminal**
del servicio `investing` (el icono `>_` en la barra de acciones):

```bash
investing-bot ingesta precios --dias 400
```

Tarda unos minutos. Debe terminar con `errores=0` y unas 12 000 barras (30
instrumentos × ~400 días hábiles). Luego el resto de fuentes:

```bash
investing-bot ingesta todos
```

Y ahora la prueba de verdad — genera el digest de hoy y **te lo manda por
Telegram**:

```bash
investing-bot digest --enviar
```

Si llega el mensaje, el sistema está completo. A partir de mañana sale solo a
las 18:15 ET sin que hagas nada.

Para verlo sin enviarlo, quita `--enviar`.

---

## Variables que controlan el arranque

Las lee `docker/arrancar.sh`, el entrypoint de la imagen.

| Variable | Por defecto | Para qué |
|---|---|---|
| `ESPERAR_BD` | `false` | Espera a que PostgreSQL acepte consultas antes de arrancar |
| `ESPERAR_BD_TIMEOUT` | `90` | Plazo máximo de esa espera, en segundos |
| `EJECUTAR_MIGRACIONES` | `false` | Aplica Alembic y siembra la whitelist |
| `SERVICIO` | *(vacío)* | `todo`, `api`, `bot` o `planificador`. Manda sobre el CMD de la imagen |

Existen porque en Easypanel no hay `depends_on`: sin `ESPERAR_BD`, el contenedor
arranca antes que la base, revienta y entra en un ciclo de reinicios. Con Docker
Compose no hacen falta —los healthchecks resuelven el orden— y por eso el
comportamiento por defecto es no hacer nada de esto.

---

## Problemas frecuentes

**El dominio devuelve 502 o no carga.** El puerto del dominio quedó en 80. El
contenedor escucha en **8000**.

**`bd_no_disponible` tras agotar el plazo.** El host o la contraseña de `URL_BD`
no coinciden con el servicio de Postgres. Cópialos de nuevo de su pantalla.
Verifica que el host lleve el prefijo del proyecto
(`thelonec_investing-bbd`, no `investing-bbd`).

**`Can't load plugin: sqlalchemy.dialects:postgres`.** No debería pasar: el
proyecto reescribe `postgres://` a `postgresql+asyncpg://` solo. Si aparece,
`URL_BD` trae un esquema raro; usa el formato `postgres://usuario:clave@host:5432/base`.

**`bot_no_configurado` en los logs.** Falta el token o el `chat_id`. Es el
comportamiento previsto: el sistema arranca sin bot y sigue ingiriendo datos.

**Reinicios en bucle con `servicio_caido servicio=bot`.** El token está mal
escrito, o el bot fue borrado en @BotFather. En modo `todo` eso tumba también el
dashboard. Vacía `TELEGRAM_BOT_TOKEN` para volver a levantar el resto mientras
lo arreglas.

**Reinicios con `servicio_termino_antes_de_tiempo`.** Uno de los tres se fue por
su cuenta sin que nadie se lo pidiera. La línea siguiente, `servicio_caido`, dice
cuál y por qué.

**No llega ningún digest.** Por orden: ¿aparece `job_registrado id=digest_diario`
en los logs? ¿está pausado el envío (`/reanudar`)? ¿está vinculado el chat
(`/start`)?

**El digest llega pero dice «Sin sugerencias hoy».** Es un resultado válido, no
un error — el sistema no fuerza operaciones. Pero si dice *«Ninguna señal tuvo
datos suficientes»*, faltan las claves de Finnhub y Reddit del paso 2.

**`presupuesto_agotado proveedor=marketaux`.** Se consumieron las 90 peticiones
diarias, casi siempre porque Finnhub lleva horas caído. No es un fallo: el tope
existe para que quede cuota mañana. Vuelve solo a medianoche UTC.

**El dashboard muestra 0 barras de precio.** Aún no corrió la ingesta. Ejecuta
el comando del paso 4.

**Las migraciones corren en cada reinicio.** Es correcto y es barato: Alembic no
hace nada si ya está en `head`, y sembrar la whitelist es idempotente
(`nuevas=0`).

---

## Apéndice: el despliegue separado

Si prefieres los cuatro servicios, la única diferencia es que `investing` lleva
`SERVICIO=api` y se añaden dos App más, con el **mismo Source, Build y bloque de
variables**, sin dominio:

| Servicio | Cambios sobre el bloque del paso 3 |
|---|---|
| `investing` | `SERVICIO=api` |
| `planificador` | `SERVICIO=planificador` y `EJECUTAR_MIGRACIONES=false` |
| `bot` | `SERVICIO=bot` y `EJECUTAR_MIGRACIONES=false` |

`EJECUTAR_MIGRACIONES=true` va **en uno solo**. Si lo pones en los tres, tres
contenedores intentan migrar a la vez contra la misma base.

Y el aviso de siempre: **sin el servicio `planificador` no llega ninguna
alerta.** El dashboard no ingiere datos ni envía nada; el bot solo contesta
cuando le preguntas. El digest sale del planificador, y es el servicio que más
se olvida crear. Es exactamente el problema que `SERVICIO=todo` elimina.

---

## Lo que este despliegue NO hace

- **No ejecuta órdenes en ningún bróker** (invariante I2). No hay ni habrá
  credenciales de bróker entre estas variables. El sistema sugiere; tú ejecutas
  a mano en tu bróker y lo anotas con `/registrar`.
- **No ha validado nada.** La FASE 2 (backtester) está pendiente: nadie ha
  comprobado que estas señales superen a comprar SPY y quedarse quieto. Cada
  digest lo advierte. Portafolio sombra, no dinero real.
