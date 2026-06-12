"""Tests for The Muse keyword→category mapping (Phase 6a)."""

from app.scrapers.the_muse import _categories_for_keywords


def test_tech_titles_map_to_software_engineering():
    cats = _categories_for_keywords(["Forward Deployed Engineer", "Solution Architect"])
    assert "Software Engineering" in cats


def test_data_scientist_maps_to_data_science():
    cats = _categories_for_keywords(["Senior Data Scientist"])
    assert "Data Science" in cats


def test_accountant_maps_to_finance_not_software():
    cats = _categories_for_keywords(["Staff Accountant", "Financial Analyst"])
    assert "Accounting and Finance" in cats
    assert "Software Engineering" not in cats


def test_product_manager_maps_to_product():
    assert _categories_for_keywords(["Product Manager"]) == ["Product Management"]


def test_no_match_returns_empty():
    # A title that maps to no category → no Muse pass (not a software default).
    assert _categories_for_keywords(["Underwater Basket Weaver"]) == []


def test_categories_are_deduped_and_capped():
    cats = _categories_for_keywords(["Software Engineer", "Backend Developer", "DevOps Engineer"])
    assert cats == ["Software Engineering"]   # all collapse to one, no dupes


def test_multiple_distinct_categories():
    cats = _categories_for_keywords(["Software Engineer", "Product Manager"])
    assert set(cats) == {"Software Engineering", "Product Management"}
