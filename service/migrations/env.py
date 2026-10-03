import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.config import settings
from src.database import Base
from src.models import *  # noqa: F401, F403 — import all models so metadata is populated

config = context.config

# Override sqlalchemy.url from application settings (avoids credentials in alembic.ini)
config.set_main_option("sqlalchemy.url", settings.database_url)

# fileConfig() REPLACES the process's logging config: it clears root handlers and
# resets the root level to alembic.ini's `WARN`. Run in-process at app startup
# (main.py `_run_migrations`) that silently undid configure_logging() for the rest
# of the process — JSON rendering gone, every info-level event (i.e. all 2xx
# access logs) dropped. Alembic's own recipe for programmatic use: let the caller
# opt out via config.attributes. CLI runs pass nothing and keep alembic's logging.
if config.attributes.get("configure_logger", True) and config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
