from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(url: str):
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("HealthOps requires PostgreSQL with psycopg, not SQLite.")
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )


def get_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker = request.app.state.session_factory
    with factory() as session:
        # Uncommitted work is rolled back on error/close.
        yield session
