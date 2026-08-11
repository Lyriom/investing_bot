# Despliegue en servidor

Guía para poner `investing_bot` en un VPS con Docker. Pensada para una máquina
pequeña (1 vCPU / 2 GB es suficiente para la FASE 0).

---

## Antes de empezar: lo que hay que entender

**El dashboard no tiene autenticación.** Muestra el estado del pipeline y, a
partir de la FASE 3, señales y portafolio. Publicarlo en internet tal cual es
publicar tus datos. Por eso `docker-compose.produccion.yml` lo ata a `127.0.0.1`
y el acceso pasa obligatoriamente por un proxy inverso con TLS y contraseña.

**La base de datos no publica puerto.** Solo es alcanzable desde la red interna
de compose. Para inspeccionarla se entra por `exec`, no por `psql` desde fuera.

**Los secretos viven en `.env` en el servidor.** Ese archivo no se versiona
(invariante I5). Si alguno falta, compose falla al arrancar en vez de usar un
valor por defecto inseguro.

---

## 1. Preparar el servidor

```bash
# Docker (script oficial)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" && newgrp docker

# Cortafuegos: solo SSH y HTTPS. Nada de 8000 ni 5432 abiertos.
sudo ufw allow OpenSSH
sudo ufw allow 443/tcp
sudo ufw --force enable
```

## 2. Traer el código

```bash
sudo mkdir -p /opt/investing_bot && sudo chown "$USER" /opt/investing_bot
git clone https://github.com/Lyriom/investing_bot.git /opt/investing_bot
cd /opt/investing_bot
```

## 3. Configurar los secretos

```bash
cp .env.example .env
chmod 600 .env

# Contraseña de base aleatoria, no una elegida a mano
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=')|" .env
sed -i "s|^ENTORNO=.*|ENTORNO=produccion|" .env

# Editar a mano el resto: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID_AUTORIZADO,
# CAPITAL_TOTAL_USD.
nano .env
```

`POSTGRES_HOST` puede quedarse como esté: el compose de producción lo fija a `db`.

## 4. Levantar

```bash
docker compose -f docker-compose.produccion.yml up -d --build
docker compose -f docker-compose.produccion.yml ps
```

Orden de arranque: `db` → `migraciones` (aplica Alembic, siembra la whitelist y
termina) → `app`, `planificador`, `bot`.

Historial inicial de precios, sin esperar al planificador:

```bash
docker compose -f docker-compose.produccion.yml run --rm app \
  investing-bot ingesta precios --dias 90
```

## 5. Proxy inverso con TLS y contraseña

Con [Caddy](https://caddyserver.com), que resuelve el certificado solo:

```bash
sudo apt install -y caddy
# Genera el hash de la contraseña:
caddy hash-password
```

`/etc/caddy/Caddyfile`:

```caddyfile
panel.tudominio.com {
    basic_auth {
        # Usuario y el hash que devolvió `caddy hash-password`
        lyriom $2a$14$...
    }
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
```

Sin este paso el dashboard solo es accesible por un túnel SSH:

```bash
ssh -L 8000:127.0.0.1:8000 usuario@servidor   # y abrir http://localhost:8000
```

---

## Operación diaria

```bash
cd /opt/investing_bot
alias ibc='docker compose -f docker-compose.produccion.yml'

ibc ps                          # estado de los servicios
ibc logs -f planificador        # seguir la ingesta
ibc logs --since 24h app        # últimas 24 h del dashboard
ibc exec db psql -U investing -d investing_bot
ibc run --rm app investing-bot ingesta precios --dias 5   # ingesta manual
```

### Actualizar a una versión nueva

```bash
cd /opt/investing_bot
git pull
ibc up -d --build          # reconstruye y reemplaza; `migraciones` corre solo
ibc logs migraciones       # confirmar que Alembic aplicó sin error
```

El planificador atiende SIGTERM: si hay una ingesta a medio camino, la deja
terminar antes de cerrar. Un despliegue no parte una corrida por la mitad.

### Respaldo de la base

La base es el activo del proyecto: contiene el histórico point-in-time, que no
se puede reconstruir después (nadie te vende los `observed_at` de ayer).

```bash
# Volcado diario a las 03:00, con retención de 14 días
cat >/etc/cron.daily/respaldo-investing <<'SH'
#!/bin/sh
set -eu
DESTINO=/var/backups/investing
mkdir -p "$DESTINO"
cd /opt/investing_bot
docker compose -f docker-compose.produccion.yml exec -T db \
  pg_dump -U investing -d investing_bot | gzip > "$DESTINO/$(date +%F).sql.gz"
find "$DESTINO" -name '*.sql.gz' -mtime +14 -delete
SH
chmod +x /etc/cron.daily/respaldo-investing
```

Restaurar:

```bash
gunzip -c /var/backups/investing/2026-08-11.sql.gz | \
  ibc exec -T db psql -U investing -d investing_bot
```

---

## La imagen

`Dockerfile` es multi-stage con dos destinos:

| Destino | Contiene | Lo usa |
|---|---|---|
| `desarrollo` | pytest, ruff, mypy, tests, instalación editable | `docker-compose.yml` |
| `produccion` | solo el venv con dependencias de runtime + Alembic | `docker-compose.produccion.yml` |

`produccion` es el último stage, así que es también el destino por defecto de
`docker build .`: quien construya sin `--target` obtiene la imagen de servidor,
no la de desarrollo.

El toolchain de compilación (`build-essential`) vive únicamente en el stage
`constructor` y nunca llega a la imagen final. El proceso corre como usuario
`investing` (uid 1000), no como root.

Construir solo la imagen:

```bash
docker build --target produccion -t investing-bot:0.1.0 .
```

---

## Qué NO hace este despliegue

- **No expone ningún puerto de base de datos.** Si necesitas conectarte con un
  cliente gráfico, haz un túnel SSH; no abras el 5432.
- **No ejecuta órdenes en ningún bróker** (invariante I2). No hay credenciales
  de bróker en `.env` ni las habrá.
- **No incluye monitorización externa.** Para la FASE 0, `docker compose ps` y
  los logs alcanzan. Si el proyecto llega a la FASE 4, vale la pena un
  healthcheck externo que avise si el digest diario deja de llegar.
