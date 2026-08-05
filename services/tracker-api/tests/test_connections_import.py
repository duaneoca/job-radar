"""Importing a LinkedIn Connections.csv.

Two fields are new here and both are less boring than they look: the profile URL
arrives in a file the user uploaded and gets rendered as an href, and the
"Connected On" date is free text that has to sort correctly.
"""

import io

from app import models

from .conftest import TEST_USER_ID

HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On"


def _csv(*rows: str, preamble: bool = False) -> bytes:
    body = "\n".join([HEADER, *rows])
    if preamble:
        # LinkedIn puts a "Notes:" blurb above the real header.
        body = "Notes:\nYou can export...\n\n" + body
    return body.encode()


def _upload(client, content: bytes, replace=True):
    return client.post(
        f"/connections/import?replace={str(replace).lower()}",
        files={"file": ("Connections.csv", io.BytesIO(content), "text/csv")},
    )


def _rows(db):
    return (db.query(models.LinkedInConnection)
              .filter(models.LinkedInConnection.user_id == TEST_USER_ID)
              .order_by(models.LinkedInConnection.first_name).all())


# ── the URL column, which we used to discard ──────────────────

def test_profile_url_is_imported(client, db):
    _upload(client, _csv("Ada,Lovelace,https://www.linkedin.com/in/ada,,Analytical,Engineer,25 Jan 2008"))
    assert _rows(db)[0].profile_url == "https://www.linkedin.com/in/ada"


def test_linkedin_preamble_is_still_skipped(client, db):
    r = _upload(client, _csv("Ada,Lovelace,https://x.test/ada,,Analytical,Engineer,25 Jan 2008",
                             preamble=True))
    assert r.json()["imported"] == 1
    assert _rows(db)[0].profile_url == "https://x.test/ada"


def test_javascript_url_is_rejected(client, db):
    """Stored XSS: this value goes straight into an href. A CSV is trivially
    hand-editable, so the scheme is allow-listed rather than filtered."""
    _upload(client, _csv("Mal,Icious,javascript:alert(document.cookie),,Evil,Hacker,25 Jan 2008"))
    row = _rows(db)[0]
    assert row.profile_url is None
    assert row.first_name == "Mal"      # the contact still imports


def test_data_url_is_rejected(client, db):
    _upload(client, _csv("Mal,Icious,\"data:text/html,<script>x</script>\",,Evil,Hacker,25 Jan 2008"))
    assert _rows(db)[0].profile_url is None


def test_relative_and_schemeless_urls_are_rejected(client, db):
    """No netloc means it isn't a profile link; rendering it would produce a
    same-origin link into our own app."""
    _upload(client, _csv("A,One,/etc/passwd,,X,Y,25 Jan 2008",
                         "B,Two,linkedin.com/in/nope,,X,Y,25 Jan 2008"))
    assert [r.profile_url for r in _rows(db)] == [None, None]


def test_absurdly_long_url_is_dropped(client, db):
    _upload(client, _csv(f"A,One,https://x.test/{'a' * 600},,X,Y,25 Jan 2008"))
    assert _rows(db)[0].profile_url is None


def test_missing_url_column_is_fine(client, db):
    """Older exports, and the file everyone already uploaded once."""
    body = b"First Name,Last Name,Company,Position,Connected On\nAda,Lovelace,Analytical,Engineer,25 Jan 2008"
    assert _upload(client, body).json()["imported"] == 1
    assert _rows(db)[0].profile_url is None


# ── the date, which has to sort ───────────────────────────────

def test_connected_on_is_parsed_into_a_date(client, db):
    _upload(client, _csv("Ada,Lovelace,,,Analytical,Engineer,25 Jan 2008"))
    row = _rows(db)[0]
    assert row.connected_on == "25 Jan 2008"      # raw text kept for display
    assert row.connected_at.isoformat() == "2008-01-25"


def test_dates_sort_chronologically_not_lexically(client, db):
    """The bug this column exists to prevent: as text, "07 Apr 2026" sorts
    before "08 Feb 2019"."""
    _upload(client, _csv("A,One,,,X,Y,07 Apr 2026",
                         "B,Two,,,X,Y,08 Feb 2019",
                         "C,Three,,,X,Y,30 Sep 2007"))
    ordered = (db.query(models.LinkedInConnection)
                 .filter(models.LinkedInConnection.user_id == TEST_USER_ID)
                 .order_by(models.LinkedInConnection.connected_at)
                 .all())
    assert [r.first_name for r in ordered] == ["C", "B", "A"]


def test_unparseable_date_keeps_the_text_and_leaves_the_sort_key_null(client, db):
    """Regional formats we haven't seen must not lose the contact or invent a
    date — a wrong date sorts wrongly and silently."""
    _upload(client, _csv("Ada,Lovelace,,,Analytical,Engineer,sometime in 2008"))
    row = _rows(db)[0]
    assert row.connected_on == "sometime in 2008"
    assert row.connected_at is None


def test_alternative_date_formats(client, db):
    _upload(client, _csv("A,One,,,X,Y,2008-01-25",
                         "B,Two,,,X,Y,01/25/2008",
                         "C,Three,,,X,Y,25 January 2008"))
    assert {r.connected_at.isoformat() for r in _rows(db)} == {"2008-01-25"}


def test_blank_date_is_not_an_error(client, db):
    _upload(client, _csv("Ada,Lovelace,,,Analytical,Engineer,"))
    assert _rows(db)[0].connected_at is None


# ── the response the table renders from ───────────────────────

def test_list_exposes_the_new_fields(client, db):
    _upload(client, _csv("Ada,Lovelace,https://x.test/ada,ada@x.test,Analytical,Engineer,25 Jan 2008"))
    row = client.get("/connections").json()[0]
    assert row["profile_url"] == "https://x.test/ada"
    assert row["connected_at"] == "2008-01-25"
    assert row["connected_on"] == "25 Jan 2008"
    assert row["email"] == "ada@x.test"


# ── the Job column ────────────────────────────────────────────
# `has_job` is the mirror of `has_contact` on the jobs list. The property that
# matters is that they agree: a job page claiming "you know someone here" while
# the connections table shows no tick (or the reverse) is worse than either
# signal alone, because it makes both untrustworthy.

import uuid as _uuid

from app.security import encrypt_api_key  # noqa: F401  (kept for parity with other tests)


def _job_for(db, company: str, status=models.JobStatus.NEW):
    job = models.Job(id=_uuid.uuid4(), external_id=str(_uuid.uuid4()), source="manual",
                     title="Eng", company=company, url=f"https://x/{_uuid.uuid4()}")
    db.add(job)
    db.flush()
    db.add(models.UserJobReview(id=_uuid.uuid4(), user_id=TEST_USER_ID,
                                job_id=job.id, status=status))
    db.commit()
    return job


def _connections(client):
    return {c["company"]: c["has_job"] for c in client.get("/connections").json()}


def test_has_job_is_true_when_a_job_shares_the_company(client, db):
    _upload(client, _csv("Ada,Lovelace,,,Acme Corp,Engineer,25 Jan 2008"))
    _job_for(db, "Acme Corp")
    assert _connections(client)["Acme Corp"] is True


def test_has_job_is_false_without_a_matching_job(client, db):
    _upload(client, _csv("Ada,Lovelace,,,Acme Corp,Engineer,25 Jan 2008"))
    _job_for(db, "Globex")
    assert _connections(client)["Acme Corp"] is False


def test_match_ignores_case_and_surrounding_space(client, db):
    _upload(client, _csv("Ada,Lovelace,,,  ACME Corp ,Engineer,25 Jan 2008"))
    _job_for(db, "acme corp")
    assert list(_connections(client).values()) == [True]


def test_substring_companies_do_NOT_match(client, db):
    """The rule that keeps this useful. Against real data, substring matching
    told the user they knew someone at EY because of a connection at Birdeye,
    and at SHI because of Blueshift."""
    _upload(client, _csv("A,One,,,Birdeye,Engineer,25 Jan 2008",
                         "B,Two,,,Blueshift,Engineer,25 Jan 2008"))
    _job_for(db, "EY")
    _job_for(db, "SHI")
    assert set(_connections(client).values()) == {False}


def test_blank_company_never_matches(client, db):
    """A connection with no company must not match a job with no company."""
    _upload(client, _csv("Ada,Lovelace,,,,Engineer,25 Jan 2008"))
    assert _connections(client) == {None: False}


def test_agrees_with_the_jobs_list(client, db):
    """The invariant. Same company, both directions, one answer."""
    _upload(client, _csv("Ada,Lovelace,,,Acme Corp,Engineer,25 Jan 2008",
                         "Bob,Stone,,,Globex,Engineer,25 Jan 2008"))
    _job_for(db, "Acme Corp")

    from_connections = _connections(client)
    from_jobs = {j["company"]: j["has_contact"] for j in client.get("/jobs").json()["items"]}

    assert from_connections["Acme Corp"] is True and from_jobs["Acme Corp"] is True
    assert from_connections["Globex"] is False        # no job there
    assert "Globex" not in from_jobs


def test_any_status_counts_including_expired(client, db):
    """It's "on my list", not "actively pursuing" — an expired posting is still
    a company you have a way into."""
    _upload(client, _csv("Ada,Lovelace,,,Acme Corp,Engineer,25 Jan 2008"))
    _job_for(db, "Acme Corp", status=models.JobStatus.EXPIRED)
    assert _connections(client)["Acme Corp"] is True


def test_another_users_jobs_do_not_count(client, db):
    _upload(client, _csv("Ada,Lovelace,,,Acme Corp,Engineer,25 Jan 2008"))
    other = models.User(id=_uuid.uuid4(), email="other@x.com", password_hash="x", is_approved=True)
    db.add(other)
    db.flush()
    job = models.Job(id=_uuid.uuid4(), external_id="o1", source="manual",
                     title="Eng", company="Acme Corp", url="https://x/o1")
    db.add(job)
    db.flush()
    db.add(models.UserJobReview(id=_uuid.uuid4(), user_id=other.id, job_id=job.id,
                                status=models.JobStatus.NEW))
    db.commit()
    assert _connections(client)["Acme Corp"] is False
