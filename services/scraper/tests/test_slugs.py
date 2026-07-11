"""candidate_slugs derivation cases."""

from app.scrapers.slugs import candidate_slugs


def test_multiword_with_suffix():
    assert candidate_slugs("Acme Corp.") == ["acmecorp", "acme-corp", "acme"]


def test_single_word():
    assert candidate_slugs("Ramp") == ["ramp"]


def test_suffix_only_stripped_variant():
    slugs = candidate_slugs("Anthropic PBC")
    assert slugs[0] == "anthropicpbc"
    assert "anthropic" in slugs


def test_punctuation_and_case():
    slugs = candidate_slugs("O'Reilly Media, Inc.")
    assert "oreillymedia" in slugs
    assert "oreilly" in slugs
    assert all(s == s.lower() for s in slugs)


def test_dedupes_and_preserves_order():
    slugs = candidate_slugs("Stripe Stripe")
    assert len(slugs) == len(set(slugs))


def test_empty_input():
    assert candidate_slugs("") == []
    assert candidate_slugs("   ") == []
