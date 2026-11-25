"""Migration 0040 copies the email policy and retention from email_credentials
into agent_settings. Runs the migration's own upgrade()/downgrade() on a
throwaway SQLite database: a copy that silently drops a user's settings would
make the agent fall back to .env defaults with no error anywhere."""

import importlib.util
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import models

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0040_agent_settings.py"


def _migration():
    spec = importlib.util.spec_from_file_location("m0040", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, fn):
    mod = _migration()
    with engine.begin() as conn:
        ops = Operations(MigrationContext.configure(conn))
        mod.op = ops   # what `from alembic import op` resolves to inside an alembic run
        getattr(mod, fn)()


def test_upgrade_copies_set_values_and_skips_empty_rows(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    # The schema as of 0039: everything except agent_settings.
    tables = [t for t in models.Base.metadata.sorted_tables if t.name != "agent_settings"]
    models.Base.metadata.create_all(engine, tables=tables)

    with_policy, with_retention, untouched = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        for uid in (with_policy, with_retention, untouched):
            conn.execute(models.User.__table__.insert(),
                         {"id": uid, "email": f"{uid}@x.com", "password_hash": "x", "is_approved": True})
        cred = models.EmailCredential.__table__
        base = {"provider": "gmail", "encrypted_blob": "x", "enabled": True}
        for row in [
            {**base, "id": uuid.uuid4(), "user_id": with_policy, "allowed_sender_domains": [],
             "require_sender_auth": False, "zero_postings_action": "unprocessed",
             "trusted_authserv_ids": ["mx.example.com"]},
            {**base, "id": uuid.uuid4(), "user_id": with_retention,
             "retention_social_days": 14, "retention_postings_days": 30},
            {**base, "id": uuid.uuid4(), "user_id": untouched},
        ]:
            conn.execute(cred.insert(), row)

    _run(engine, "upgrade")

    with engine.connect() as conn:
        rows = {r["user_id"]: r for r in conn.execute(sa.text("SELECT * FROM agent_settings")).mappings()}
    # SQLite stores Uuid() as 32-hex text; normalise both sides.
    as_uuid = {uuid.UUID(k.hex if isinstance(k, uuid.UUID) else k): v for k, v in rows.items()}

    assert set(as_uuid) == {with_policy, with_retention}
    p = as_uuid[with_policy]
    assert json.loads(p["allowed_sender_domains"]) == []          # "allow any" survives the copy
    assert p["require_sender_auth"] in (0, False)
    assert p["zero_postings_action"] == "unprocessed"
    assert json.loads(p["trusted_authserv_ids"]) == ["mx.example.com"]
    r = as_uuid[with_retention]
    assert (r["retention_social_days"], r["retention_postings_days"]) == (14, 30)
    assert r["sorter_enabled"] is None and r["staffing_enabled"] is None

    _run(engine, "downgrade")
    assert "agent_settings" not in sa.inspect(engine).get_table_names()
