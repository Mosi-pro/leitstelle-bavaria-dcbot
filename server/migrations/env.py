"""Alembic-Umgebung: verwendet dieselbe STRATO-MySQL-Konfiguration wie die Anwendung (.env)."""
from alembic import context

from lbc.core.db import create_db_engine
from lbc.core.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    from lbc.core.config import get_settings
    from lbc.core.db import build_url

    context.configure(url=build_url(get_settings()).render_as_string(hide_password=False),
                      target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine()
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
