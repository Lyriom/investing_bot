"""Tests de configuracion. Incluye la garantia de que no hay secretos en el repo."""

from __future__ import annotations

from investing_bot.config import RAIZ_PROYECTO, Configuracion


def test_url_bd_se_construye_desde_las_partes() -> None:
    config = Configuracion(
        postgres_host="db",
        postgres_port=5432,
        postgres_db="investing_bot",
        postgres_user="investing",
        postgres_password="clave",
        _env_file=None,
    )
    assert config.url_bd_async == "postgresql+asyncpg://investing:clave@db:5432/investing_bot"


def test_url_bd_explicita_tiene_prioridad() -> None:
    config = Configuracion(url_bd="postgresql+asyncpg://otro@host/db", _env_file=None)
    assert config.url_bd_async == "postgresql+asyncpg://otro@host/db"


def test_telegram_requiere_token_y_chat() -> None:
    assert not Configuracion(_env_file=None).telegram_configurado
    assert not Configuracion(telegram_bot_token="abc", _env_file=None).telegram_configurado
    assert not Configuracion(telegram_chat_id_autorizado=42, _env_file=None).telegram_configurado
    assert Configuracion(
        telegram_bot_token="abc", telegram_chat_id_autorizado=42, _env_file=None
    ).telegram_configurado


def test_el_archivo_de_whitelist_existe() -> None:
    assert Configuracion(_env_file=None).archivo_whitelist.is_file()


def test_env_esta_ignorado_por_git() -> None:
    """Invariante I5: los secretos nunca entran al repositorio."""
    gitignore = (RAIZ_PROYECTO / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert "!.env.example" in gitignore


def test_env_example_no_trae_valores_reales() -> None:
    """`.env.example` documenta las claves; jamas sus valores."""
    ejemplo = RAIZ_PROYECTO / ".env.example"
    claves_secretas = (
        "TELEGRAM_BOT_TOKEN",
        "POSTGRES_PASSWORD",
        "FINNHUB_API_KEY",
        "MARKETAUX_API_KEY",
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
    )
    lineas = ejemplo.read_text(encoding="utf-8").splitlines()
    presentes = set()
    for linea in lineas:
        if "=" not in linea or linea.strip().startswith("#"):
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        if clave in claves_secretas:
            presentes.add(clave)
            assert valor.strip() == "", f"{clave} tiene un valor en .env.example"
    assert presentes == set(claves_secretas)
