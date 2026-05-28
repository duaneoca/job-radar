"""
Shared test fixtures — uses an in-memory SQLite DB so tests
run without a real PostgreSQL instance.

IMPORTANT: DATABASE_URL must be set before app.database is imported,
because it creates the engine at module level.
"""

import os
import uuid

# Override DB URL before any app modules are imported
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.deps import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

TEST_DATABASE_URL = "sqlite:///./test.db"
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_user(db):
    """Create an approved test user in the DB."""
    user = User(
        id=TEST_USER_ID,
        email="test@example.com",
        password_hash=hash_password("testpassword"),
        full_name="Test User",
        is_approved=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def client(db, test_user):
    """Test client with DB and auth overrides applied."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        return test_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
