#!/usr/bin/env python3
"""Harvest candidate job links from UNSEEN emails in an IMAP folder.

Designed for Proton Bridge (local IMAP on 127.0.0.1) but works against any
IMAP server. Stdlib only — nothing to install.

Read-only by default: `list` uses BODY.PEEK and SELECTs the folder read-only,
so listing NEVER marks a message \\Seen. The only mutating command is
`mark-seen`, which the skill calls *after* a message has been fully processed
(every job link imported or confirmed-duplicate). On any error mid-message,
don't mark it — it stays UNSEEN and is retried next run.

Config via environment (see env.example):
  JR_IMAP_HOST    default 127.0.0.1
  JR_IMAP_PORT    default 1143
  JR_IMAP_USER    (required)
  JR_IMAP_PASS    (required)
  JR_IMAP_FOLDER  default "Job Postings"
  JR_IMAP_SEC     one of starttls | ssl | plain   (default starttls — Proton Bridge)

Usage:
  imap_fetch.py list            -> JSON per email: {uid, subject, sender, date,
                                   jobs:[{source,external_id,url,via}],
                                   unsupported:[{url,domain}], junk_count}
  imap_fetch.py mark-seen UID   -> marks message \\Seen (mutating)

Output is JSON on stdout; diagnostics go to stderr.
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
import ssl
import sys
from email.header import decode_header, make_header
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

# --- config -----------------------------------------------------------------

HOST = os.environ.get("JR_IMAP_HOST", "127.0.0.1")
PORT = int(os.environ.get("JR_IMAP_PORT", "1143"))
USER = os.environ.get("JR_IMAP_USER", "")
PASS = os.environ.get("JR_IMAP_PASS", "")
FOLDER = os.environ.get("JR_IMAP_FOLDER", "Job Postings")
SEC = os.environ.get("JR_IMAP_SEC", "starttls").lower()

# The 6 destination sites the Job Radar bookmarklet can extract from.
SUPPORTED_SITES = ("linkedin", "dice", "builtin", "monster", "ziprecruiter", "indeed")

# Footer / settings / social / search links — never postings. Substring match on
# the (unwrapped) lowercased URL. Conservative: better to let a stray non-job
# reach the bookmarklet (which rejects it) than to drop a real posting.
JUNK_SUBSTRINGS = (
    "unsubscribe", "/preferences", "email-settings", "email_settings",
    "email_preferences", "manage-preferences", "manage_preferences",
    "notification", "optout", "opt-out", "/settings", "/account", "privacy",
    "/terms", "/help", "/login", "signin", "sign-in",
    "jobs/search", "jobs/alerts", "/jobs?", "/widgets/", "/profile/",
    "/feed", "/mynetwork", "/messaging", "jotform.com", "/company/",
)
# Hosts that are always junk regardless of path (social, app stores, app links,
# and email-management endpoints that masquerade as content links).
JUNK_HOSTS = (
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "apps.apple.com", "play.google.com", "onelink.me", "engage.indeed.com",
)
# Opaque per-provider click trackers: the destination is hidden behind a token,
# so we can't canonicalize — but the tracker domain tells us which supported
# site it lands on. These are navigated in the browser to resolve. host -> source.
OPAQUE_TRACKERS = {
    "cts.indeed.com": "indeed",
    "click.monster.com": "monster",
    "elinks.dice.com": "dice",
}


def log(*a):
    print(*a, file=sys.stderr)


# --- connection -------------------------------------------------------------

def connect() -> imaplib.IMAP4:
    if not USER or not PASS:
        log("ERROR: JR_IMAP_USER / JR_IMAP_PASS must be set (see env.example)")
        sys.exit(2)
    # Proton Bridge serves a self-signed cert on localhost — don't verify there.
    ctx = ssl._create_unverified_context()
    if SEC == "ssl":
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=ctx)
    else:
        conn = imaplib.IMAP4(HOST, PORT)
        if SEC == "starttls":
            conn.starttls(ssl_context=ctx)
    conn.login(USER, PASS)
    return conn


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


# --- link extraction --------------------------------------------------------

class _AnchorHarvester(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for k, v in attrs:
                if k.lower() == "href" and v:
                    self.hrefs.append(v.strip())


_URL_RE = re.compile(r"https?://[^\s<>\"'\])}]+", re.IGNORECASE)


def harvest_raw_links(msg: email.message.Message) -> list[str]:
    """All http(s) links in the email, deduped, order-preserved: prefer
    <a href> in the HTML part, fall back to bare URLs in the plain-text part.
    No classification here — that's classify_link's job."""
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            continue
        (html_parts if ctype == "text/html" else text_parts).append(decoded)

    raw: list[str] = []
    for html in html_parts:
        p = _AnchorHarvester()
        try:
            p.feed(html)
        except Exception:
            pass
        raw.extend(p.hrefs)
    if not raw:  # no HTML anchors — scrape URLs out of the plain text
        for text in text_parts:
            raw.extend(_URL_RE.findall(text))

    seen: set[str] = set()
    out: list[str] = []
    for url in raw:
        if url.lower().startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _unwrap(url: str) -> str:
    """Undo click-tracker wrappers that embed the real, percent-encoded URL.
    AWS SES (Built In) wraps it after `/L0/`, up to the next unencoded slash."""
    if "awstrack.me/" in url and "/L0/" in url:
        enc = url.split("/L0/", 1)[1].split("/", 1)[0]
        dec = unquote(enc)
        if dec.lower().startswith("http"):
            return dec
    return url


def classify_link(raw: str) -> dict | None:
    """Map one harvested link to a job, an unsupported destination, or junk.

    Returns one of:
      {"kind": "job", "source", "external_id"|None, "url", "via": "canonical"|"opaque"}
      {"kind": "unsupported", "url", "domain"}
      None                                                # junk / not a posting

    Deterministic, no network. `canonical` links carry an id and a clean URL
    (deduped downstream); `opaque` links are tracker URLs that must be opened in
    the browser to resolve — the bookmarklet classifies them on arrival.
    """
    if not raw.lower().startswith("http"):
        return None
    url = _unwrap(raw)
    low = url.lower()
    host = urlparse(low).netloc

    # 1. Canonicalizable job patterns (id visible in the URL) ------------------
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", low)
    if m:
        jid = m.group(1)
        return {"kind": "job", "source": "linkedin", "external_id": jid,
                "url": f"https://www.linkedin.com/jobs/view/{jid}", "via": "canonical"}
    m = re.search(r"builtin\.com/job/([^/?#]+)/(\d+)", low)
    if m:
        slug, jid = m.group(1), m.group(2)
        return {"kind": "job", "source": "builtin", "external_id": jid,
                "url": f"https://builtin.com/job/{slug}/{jid}", "via": "canonical"}
    m = re.search(r"[?&]jk=([0-9a-f]+)", low)
    if "indeed.com" in host and m:
        return {"kind": "job", "source": "indeed", "external_id": m.group(1),
                "url": f"https://www.indeed.com/viewjob?jk={m.group(1)}", "via": "canonical"}

    # 2. Junk (settings/footer/social/search) — before opaque-tracker mapping --
    if any(h in host for h in JUNK_HOSTS):
        return None
    if any(s in low for s in JUNK_SUBSTRINGS):
        return None

    # 3. Opaque trackers that redirect to a supported site -> navigate ---------
    for dom, src in OPAQUE_TRACKERS.items():
        if host == dom or host.endswith("." + dom):
            return {"kind": "job", "source": src, "external_id": None,
                    "url": raw, "via": "opaque"}
    if host.endswith("ziprecruiter.com") and re.search(r"/e?km/", low):
        return {"kind": "job", "source": "ziprecruiter", "external_id": None,
                "url": raw, "via": "opaque"}

    # 4. Anything else is an unsupported destination (logged, not imported) ----
    return {"kind": "unsupported", "url": url, "domain": host or "?"}


def group_links(raw_links: list[str]) -> dict:
    """Classify a message's links into jobs / unsupported / junk count.
    Canonical jobs dedup by (source, id); opaque jobs dedup by url."""
    jobs: list[dict] = []
    seen_jobs: set[tuple] = set()
    unsupported: list[dict] = []
    seen_unsup: set[str] = set()
    junk = 0
    for link in raw_links:
        c = classify_link(link)
        if c is None:
            junk += 1
        elif c["kind"] == "job":
            key = (c["source"], c["external_id"]) if c["external_id"] else ("url", c["url"])
            if key not in seen_jobs:
                seen_jobs.add(key)
                jobs.append(c)
        else:  # unsupported
            if c["url"] not in seen_unsup:
                seen_unsup.add(c["url"])
                unsupported.append(c)
    return {"jobs": jobs, "unsupported": unsupported, "junk_count": junk}


# --- commands ---------------------------------------------------------------

def cmd_list() -> int:
    conn = connect()
    try:
        # read-only SELECT: guarantees listing can't change flags
        conn.select(_quote(FOLDER), readonly=True)
        typ, data = conn.uid("search", None, "UNSEEN")
        if typ != "OK":
            log(f"ERROR: SEARCH failed: {typ} {data}")
            return 1
        uids = data[0].split() if data and data[0] else []
        results = []
        for uid in uids:
            typ, fetched = conn.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not fetched or not fetched[0]:
                log(f"WARN: fetch failed for uid {uid!r}")
                continue
            msg = email.message_from_bytes(fetched[0][1])
            grouped = group_links(harvest_raw_links(msg))
            results.append({
                "uid": uid.decode(),
                "subject": _decode(msg.get("Subject")),
                "sender": _decode(msg.get("From")),
                "date": _decode(msg.get("Date")),
                **grouped,
            })
        print(json.dumps(results, indent=2))
        return 0
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def cmd_mark_seen(uid: str) -> int:
    conn = connect()
    try:
        conn.select(_quote(FOLDER))  # writable
        typ, data = conn.uid("store", uid, "+FLAGS", r"(\Seen)")
        if typ != "OK":
            log(f"ERROR: STORE \\Seen failed for uid {uid}: {typ} {data}")
            return 1
        log(f"marked \\Seen: uid {uid}")
        return 0
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _quote(name: str) -> str:
    # IMAP mailbox names with spaces must be quoted.
    return '"' + name.replace('"', '\\"') + '"'


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("list", "mark-seen"):
        log(__doc__)
        return 2
    if argv[1] == "list":
        return cmd_list()
    if len(argv) < 3:
        log("usage: imap_fetch.py mark-seen UID")
        return 2
    return cmd_mark_seen(argv[2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
