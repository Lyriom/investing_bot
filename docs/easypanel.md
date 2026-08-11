# Despliegue en Easypanel

Easypanel no usa `docker-compose.yml` para los servicios de tipo **App**: cada
servicio es **un contenedor**, y no existe `depends_on`. Por eso el despliegue
se arma con **cuatro servicios** y se controla por variables de entorno.

| Servicio | Tipo | Qué hace | ¿Dominio? |
|---|---|---|---|
| `investing-bbd` | Postgres | La base de datos | No |
| `investing` | App | Dashboard. **Además aplica las migraciones al arrancar** | Sí, puerto 8000 |
| `planificador` | App | APScheduler: ingesta + **el digest de las 18:15 ET** | No |
| `bot` | App | Bot de Telegram: responde `/hoy`, `/desglose`, `/pausar` | No |

Los tres servicios App usan **la misma imagen y el mismo repo**. Lo único que
cambia entre ellos es `SERVICIO` y `EJECUTAR_MIGRACIONES`.

> **Sin `planificador` no llega ninguna alerta.** El dashboard no ingiere datos
> ni envía nada; el bot solo contesta cuando le preguntas. El digest diario sale
> del planificador. Es el servicio que más gente olvida crear.

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

| Dónde | Qué sacas |
|---|---|
| **@BotFather** en Telegram → `/newbot` | `TELEGRAM_BOT_TOKEN` |
| **@userinfobot** en Telegram → le escribes cualquier cosa | `TELEGRAM_CHAT_ID_AUTORIZADO` (tu número) |
| [finnhub.io/register](https://finnhub.io/register) (gratis) | `FINNHUB_API_KEY` → señal S1, peso 0.40 |
| [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) → **create app** tipo **script** | `REDDIT_CLIENT_ID` (bajo el nombre) y `REDDIT_CLIENT_SECRET` → señal S2, peso 0.25 |

La señal S3 (Congreso, peso 0.15) **no tiene fuente viva hoy**: los datasets de
stock-watcher devuelven 403 desde agosto de 2026. No hay clave que conseguir.

---

## 3. Configurar el servicio `investing` (dashboard)

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

Este es el bloque completo. Es **el mismo para los tres servicios App** salvo
las dos líneas marcadas, así que consérvalo a mano.

```env
ENTORNO=produccion
NIVEL_LOG=INFO

URL_BD=postgres://postgres:LA_CLAVE_DEL_PANEL@thelonec_investing-bbd:5432/postgres

ESPERAR_BD=true
ESPERAR_BD_TIMEOUT=120
EJECUTAR_MIGRACIONES=true    # <-- SOLO en este servicio
SERVICIO=api                 # <-- cambia en los otros dos

TELEGRAM_BOT_TOKEN=TU_TOKEN_DE_BOTFATHER
TELEGRAM_CHAT_ID_AUTORIZADO=TU_CHAT_ID

FINNHUB_API_KEY=TU_CLAVE_FINNHUB
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

`EJECUTAR_MIGRACIONES=true` va **solo aquí**. Este servicio espera a que la base
responda, aplica Alembic, siembra los 30 instrumentos y recién entonces levanta
el dashboard. Si lo pones también en los otros dos, tres contenedores intentan
migrar a la vez contra la misma base.

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
Uvicorn running on http://0.0.0.0:8000
```

---

## 4. Servicio `planificador`

**+ Service → App**, nombre `planificador`. Mismo Source y mismo Build que
`investing`. **Sin dominio** (no sirve HTTP).

Environment: el mismo bloque del paso 3, con dos cambios:

```env
SERVICIO=planificador
EJECUTAR_MIGRACIONES=false
```

Es el servicio que hace que el sistema esté vivo. Registra cinco jobs:

| Job | Cuándo |
|---|---|
| Ingesta de precios | 17:00 ET, lunes a viernes |
| Ingesta de noticias | cada 4 h |
| Ingesta de Reddit | cada 6 h |
| Ingesta del Congreso | 06:30 ET |
| **Digest diario** | **18:15 ET, lunes a viernes** (17:15 en Ecuador) |

Al arrancar los imprime con su próxima ejecución:

```
job_registrado  id=digest_diario  proxima_ejecucion=2026-08-11 18:15:00-04:00
```

Si esa línea no aparece en los logs, el digest no va a salir.

---

## 5. Servicio `bot`

**+ Service → App**, nombre `bot`. Mismo Source y Build. Sin dominio.

```env
SERVICIO=bot
EJECUTAR_MIGRACIONES=false
```

El bot **solo atiende el `chat_id` de la whitelist**. Cualquier otro chat recibe
silencio, no un mensaje de error. Con `TELEGRAM_CHAT_ID_AUTORIZADO=0` se niega a
arrancar, en vez de atender a cualquiera.

Si dejas el token vacío, el servicio registra el motivo y termina con código 0.
Eso es esperado: el resto del sistema sigue funcionando sin Telegram.

Comandos disponibles:

| Comando | Qué hace |
|---|---|
| `/start` | Vincula el chat |
| `/hoy` | Reenvía el digest del día |
| `/desglose SYMBOL` | El detalle numérico de cada componente del score |
| `/registrar` | Anota una operación que ejecutaste a mano |
| `/pausar` / `/reanudar` | Kill switch: deja de enviar (sigue calculando) |

---

## 6. Cargar el historial y probar

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
| `SERVICIO` | *(vacío)* | `api`, `bot` o `planificador`. Manda sobre el CMD de la imagen |

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

**El servicio `bot` aparece detenido con código 0.** Falta el token o el
`chat_id`. Es el comportamiento previsto, no un fallo.

**No llega ningún digest.** Por orden: ¿existe el servicio `planificador`?
¿aparece `job_registrado id=digest_diario` en sus logs? ¿está pausado el envío
(`/reanudar`)? ¿está vinculado el chat (`/start`)?

**El digest llega pero dice «Sin sugerencias hoy».** Es un resultado válido, no
un error — el sistema no fuerza operaciones. Pero si dice *«Ninguna señal tuvo
datos suficientes»*, faltan las claves de Finnhub y Reddit del paso 2.

**El dashboard muestra 0 barras de precio.** Aún no corrió la ingesta. Ejecuta
el comando del paso 6.

**Las migraciones corren en cada reinicio.** Es correcto y es barato: Alembic no
hace nada si ya está en `head`, y sembrar la whitelist es idempotente
(`nuevas=0`).

---

## Lo que este despliegue NO hace

- **No ejecuta órdenes en ningún bróker** (invariante I2). No hay ni habrá
  credenciales de bróker entre estas variables. El sistema sugiere; tú ejecutas
  a mano en tu bróker y lo anotas con `/registrar`.
- **No ha validado nada.** La FASE 2 (backtester) está pendiente: nadie ha
  comprobado que estas señales superen a comprar SPY y quedarse quieto. Cada
  digest lo advierte. Portafolio sombra, no dinero real.
