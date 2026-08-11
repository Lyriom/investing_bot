# Despliegue en Easypanel

Easypanel no usa `docker-compose.yml` para los servicios de tipo **App**: cada
servicio es **un contenedor**, y no existe `depends_on`. Por eso el despliegue
se arma con **cuatro servicios** y se controla por variables de entorno.

| Servicio | Tipo | Qué hace |
|---|---|---|
| `db` | Postgres | La base de datos |
| `investing` | App | Dashboard. **Además aplica las migraciones al arrancar** |
| `planificador` | App | APScheduler: ingesta diaria de precios a las 17:00 ET |
| `bot` | App | Bot de Telegram |

Los tres servicios App usan **la misma imagen y el mismo repo**. Lo único que
cambia entre ellos es la variable `SERVICIO`.

---

## 1. Crear la base de datos

En el proyecto (`thelonec`), **+ Service → Postgres**.

- **Name**: `db`
- Deja que genere la contraseña.
- **Create** y luego **Deploy**.

Cuando termine, entra al servicio `db` y copia de su pantalla:

- la **contraseña**
- el **host interno**, que en Easypanel tiene la forma `<proyecto>_<servicio>`;
  con este ejemplo sería `thelonec_db`

> Copia el host y la contraseña de la pantalla del servicio, no de aquí. Si tu
> versión de Easypanel muestra una **Connection URL** completa, mejor: se puede
> pegar tal cual, incluso en formato `postgres://` — el proyecto reescribe el
> driver solo.

No expongas la base con un dominio. Solo la usan los otros servicios del proyecto.

---

## 2. Configurar el servicio `investing` (dashboard)

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

Pega esto, sustituyendo los cuatro valores marcados:

```env
ENTORNO=produccion
NIVEL_LOG=INFO

URL_BD=postgres://postgres:LA_CLAVE_DEL_PANEL@thelonec_db:5432/postgres

ESPERAR_BD=true
ESPERAR_BD_TIMEOUT=120
EJECUTAR_MIGRACIONES=true
SERVICIO=api

TELEGRAM_BOT_TOKEN=TU_TOKEN_DE_BOTFATHER
TELEGRAM_CHAT_ID_AUTORIZADO=TU_CHAT_ID

CAPITAL_TOTAL_USD=150
ZONA_HORARIA_MERCADO=America/New_York
ZONA_HORARIA_OPERADOR=America/Guayaquil
```

Ajusta `URL_BD` con el usuario, la contraseña, el host y el nombre de base que
muestre tu servicio `db` — Easypanel suele crear la base y el usuario como
`postgres`, pero confírmalo en su pantalla.

`EJECUTAR_MIGRACIONES=true` va **solo aquí**. Este servicio espera a que la base
responda, aplica Alembic, siembra los 30 instrumentos y recién entonces levanta
el dashboard.

### Domains

- **Add domain** con tu dominio (o el `*.easypanel.host` que ofrece el panel).
- **Port: 8000**
- Activa **HTTPS**.

### Security ⚠️

**El dashboard no tiene autenticación.** Muestra el estado del pipeline y, desde
la FASE 3, señales y portafolio. En la pestaña **Security** activa **Basic Auth**
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

## 3. Servicio `planificador`

**+ Service → App**, nombre `planificador`. Mismo Source y mismo Build que
`investing`. **Sin dominio** (no sirve HTTP).

Environment: el mismo bloque, con dos cambios:

```env
SERVICIO=planificador
EJECUTAR_MIGRACIONES=false
```

Sin este servicio no hay ingesta diaria: la base se queda con los datos del día
que desplegaste y nada más.

---

## 4. Servicio `bot`

**+ Service → App**, nombre `bot`. Mismo Source y Build. Sin dominio.

```env
SERVICIO=bot
EJECUTAR_MIGRACIONES=false
```

### Token y chat autorizado

1. En Telegram, habla con **@BotFather** → `/newbot` → copia el token.
2. Escríbele algo a **@userinfobot** para obtener tu `chat_id` numérico.
3. Ponlos en `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID_AUTORIZADO` **en los tres
   servicios** (el dashboard no los usa hoy, pero los va a necesitar en la FASE 3
   para enviar el digest).

El bot **solo atiende ese `chat_id`**. Cualquier otro chat recibe silencio. Y con
`TELEGRAM_CHAT_ID_AUTORIZADO=0` se niega a arrancar, en vez de atender a
cualquiera.

Si dejas el token vacío, el servicio registra el motivo y termina con código 0.
Eso es esperado: el resto del sistema sigue funcionando sin Telegram.

---

## 5. Cargar el historial de precios

El planificador solo corre a las 17:00 ET. Para no esperar, abre la **terminal**
del servicio `investing` (el icono `>_` en la barra de acciones) y ejecuta:

```bash
investing-bot ingesta precios --dias 90
```

Debe terminar con `leidas=2700 nuevas=2700 errores=0`. Después, en el dashboard,
los 30 instrumentos aparecen con 90 barras cada uno.

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

**`bd_no_disponible` tras agotar el plazo.** El host o la contraseña de `URL_BD`
no coinciden con el servicio `db`. Cópialos de nuevo de su pantalla. Verifica que
el host lleve el prefijo del proyecto (`thelonec_db`, no `db`).

**`Can't load plugin: sqlalchemy.dialects:postgres`.** No debería pasar: el
proyecto reescribe `postgres://` a `postgresql+asyncpg://` solo. Si aparece,
`URL_BD` trae un esquema raro; usa el formato `postgres://usuario:clave@host:5432/base`.

**El servicio `bot` aparece detenido con código 0.** Falta el token o el
`chat_id`. Es el comportamiento previsto, no un fallo.

**El dashboard muestra 0 barras de precio.** Aún no corrió la ingesta. Ejecuta el
comando del paso 5.

**Las migraciones corren en cada reinicio.** Es correcto y es barato: Alembic no
hace nada si ya está en `head`, y sembrar la whitelist es idempotente
(`nuevas=0`).

---

## Lo que este despliegue NO hace

- **No ejecuta órdenes en ningún bróker** (invariante I2). No hay ni habrá
  credenciales de bróker entre estas variables.
- **No respalda la base.** Easypanel tiene backups por servicio: actívalos en el
  servicio `db`. La base guarda el histórico point-in-time, que no se puede
  reconstruir después — nadie te vende los `observed_at` de ayer.
