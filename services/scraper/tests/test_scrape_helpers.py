"""Tests for scraper helper logic (no network)."""

from app.main import _ensure_remote_pass


def test_appends_remote_when_only_concrete_cities():
    locs = ["Oakland, CA", "San Francisco, CA"]
    result = _ensure_remote_pass(locs)
    assert result == ["Oakland, CA", "San Francisco, CA", "Remote"]


def test_no_duplicate_when_remote_already_present():
    locs = ["Remote", "Austin, TX"]
    assert _ensure_remote_pass(locs) == ["Remote", "Austin, TX"]


def test_recognizes_anywhere_alias_case_insensitive():
    assert _ensure_remote_pass(["ANYWHERE"]) == ["ANYWHERE"]
    assert _ensure_remote_pass(["  remote  "]) == ["  remote  "]


def test_empty_list_gets_remote_pass():
    assert _ensure_remote_pass([]) == ["Remote"]
