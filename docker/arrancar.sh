#!/bin/sh
# Punto de entrada de la imagen.
#
# Existe para poder desplegar en plataformas donde cada servicio es un
# contenedor suelto (Easypanel, Railway, Fly) y no hay `depends_on` ni un
# contenedor de migraciones que corra antes que los demas. Todo se controla
# por variables de entorno, porque en esos paneles cambiar una variable es
# mas comodo —y menos fragil— que sobreescribir el comando.
#
#   ESPERAR_BD=true            espera a que PostgreSQL acepte consultas
#   ESPERAR_BD_TIMEOUT=90      plazo maximo de esa espera, en segundos
#   EJECUTAR_MIGRACIONES=true  aplica Alembic y siembra la whitelist
#   SERVICIO=api|bot|planificador   que proceso levantar
#
# Sin ninguna de ellas, el comportamiento es el de siempre: se ejecuta el CMD
# de la imagen. Por eso docker-compose, que si tiene `depends_on` y un
# servicio de migraciones dedicado, no necesita tocar nada.

set -eu

if [ "${ESPERAR_BD:-false}" = "true" ]; then
    investing-bot esperar-bd --timeout "${ESPERAR_BD_TIMEOUT:-90}"
fi

# Solo un servicio debe migrar. Poner esto en dos a la vez no corrompe nada
# (Alembic toma un lock y `sembrar` es idempotente), pero no tiene sentido.
if [ "${EJECUTAR_MIGRACIONES:-false}" = "true" ]; then
    investing-bot migrar
    investing-bot sembrar
fi

if [ -n "${SERVICIO:-}" ]; then
    exec investing-bot "$SERVICIO"
fi

exec "$@"
