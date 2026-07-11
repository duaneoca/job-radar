"""Tests for the internal /criteria/scraper/user-configs endpoint (Phase 3)."""

import uuid

from app import models
from app.security import encrypt_api_key

from .conftest import TEST_USER_ID
import json


def _make_criteria(db, titles, locations, work_style="any"):
    c = models.Criteria(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        is_active=True,
        job_titles=titles,
        search_locations=locations,
        work_style=work_style,
    )
    db.add(c)
    db.commit()
    return c


def test_user_config_includes_decrypted_adzuna(client, db, test_user):
    _make_criteria(db, ["Forward Deployed Engineer"], ["Oakland, CA"], "remote")
    db.add(models.UserAPIKey(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        provider=models.LLMProvider.ADZUNA,
        encrypted_key=encrypt_api_key(json.dumps({"app_id": "app123", "app_key": "secretkey"})),
    ))
    db.commit()

    resp = client.get("/criteria/scraper/user-configs")
    assert resp.status_code == 200
    data = resp.json()
    cfg = next(c for c in data if c["user_id"] == str(TEST_USER_ID))
    assert cfg["job_titles"] == ["Forward Deployed Engineer"]
    assert cfg["search_locations"] == ["Oakland, CA"]
    assert cfg["work_style"] == "remote"
    assert cfg["adzuna"] == {"app_id": "app123", "app_key": "secretkey"}


def test_user_config_adzuna_null_when_no_key(client, db, test_user):
    _make_criteria(db, ["Solution Architect"], ["Remote"])

    resp = client.get("/criteria/scraper/user-configs")
    assert resp.status_code == 200
    cfg = next(c for c in resp.json() if c["user_id"] == str(TEST_USER_ID))
    assert cfg["adzuna"] is None


def test_user_config_falls_back_to_legacy_locations(client, db, test_user):
    c = models.Criteria(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        is_active=True,
        job_titles=["Data Scientist"],
        search_locations=None,
        locations=["Austin, TX"],   # legacy column
    )
    db.add(c)
    db.commit()

    resp = client.get("/criteria/scraper/user-configs")
    cfg = next(c for c in resp.json() if c["user_id"] == str(TEST_USER_ID))
    assert cfg["search_locations"] == ["Austin, TX"]


def test_inactive_criteria_excluded(client, db, test_user):
    c = models.Criteria(
        id=uuid.uuid4(), user_id=TEST_USER_ID, is_active=False,
        job_titles=["X"], search_locations=["Y"],
    )
    db.add(c)
    db.commit()

    resp = client.get("/criteria/scraper/user-configs")
    assert all(cfg["user_id"] != str(TEST_USER_ID) for cfg in resp.json())


def test_user_config_includes_target_companies(client, db, test_user):
    c = _make_criteria(db, ["Platform Engineer"], ["Remote"])
    c.target_companies = ["Ramp", "Anthropic"]
    db.commit()

    resp = client.get("/criteria/scraper/user-configs")
    cfg = next(c for c in resp.json() if c["user_id"] == str(TEST_USER_ID))
    assert cfg["target_companies"] == ["Ramp", "Anthropic"]


def test_user_config_target_companies_defaults_empty(client, db, test_user):
    _make_criteria(db, ["Platform Engineer"], ["Remote"])

    resp = client.get("/criteria/scraper/user-configs")
    cfg = next(c for c in resp.json() if c["user_id"] == str(TEST_USER_ID))
    assert cfg["target_companies"] == []
