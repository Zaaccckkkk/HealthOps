import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from healthops.auth import hash_token
from healthops.config import Settings
from healthops.db import build_engine
from healthops.main import create_app
from healthops.models import ApiToken, User


@pytest.fixture
def record():
    path = Path(__file__).resolve().parents[1] / "examples/observations.synthetic.json"
    return json.loads(path.read_text())["observations"][0]


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("HEALTHOPS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Use docker compose --profile test run --rm test for real PostgreSQL tests.")
    if make_url(url).database != "healthops_test":
        pytest.fail("Refusing integration tests: dedicated database must be named healthops_test.")
    return url


@pytest.fixture(scope="session")
def migrated_database(database_url):
    # Migrate using the same Alembic path as deployment, not Base.metadata.create_all().
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HEALTHOPS_DATABASE_URL", database_url)
        patch.setenv("HEALTHOPS_ENVIRONMENT", "local")
        command.upgrade(Config("alembic.ini"), "head")
    yield database_url


@pytest.fixture
def engine(migrated_database):
    instance = build_engine(migrated_database)
    yield instance
    instance.dispose()


@pytest.fixture
def credentials(engine):
    # New isolated users per test; no truncation or deletion of any existing database.
    headers = []
    for name in ("synthetic-alice", "synthetic-bob"):
        raw = secrets.token_urlsafe(32)
        with Session(engine) as session:
            user = User(display_name=name)
            session.add(user)
            session.flush()
            session.add(
                ApiToken(
                    user_id=user.id,
                    token_hash=hash_token(raw),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            session.commit()
        headers.append({"Authorization": f"Bearer {raw}"})
    return headers


@pytest.fixture
def client(migrated_database):
    settings = Settings(database_url=migrated_database, environment="local")
    with TestClient(create_app(settings)) as instance:
        yield instance
