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
  imap_fetch.py list            -> JSON: [{uid, subject, sender, date, links: [...]}]
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

# --- config -----------------------------------------------------------------

HOST = os.environ.get("JR_IMAP_HOST", "127.0.0.1")
PORT = int(os.environ.get("JR_IMAP_PORT", "1143"))
USER = os.environ.get("JR_IMAP_USER", "")
PASS = os.environ.get("JR_IMAP_PASS", "")
FOLDER = os.environ.get("JR_IMAP_FOLDER", "Job Postings")
SEC = os.environ.get("JR_IMAP_SEC", "starttls").lower()

# Links we never want to open — settings/footer/social/auth, not job postings.
# This is only a *cost* filter (skip opening a browser tab); the real
# "is this a job?" decision happens at the destination via the bookmarklet's
# own URL guards. Keep it conservative so we never drop a real posting.
JUNK_SUBSTRINGS = (
    "unsubscribe", "/preferences", "email_preferences", "manage-preferences",
    "manage_preferences", "notification-settings", "optout", "opt-out",
    "/settings", "/account", "privacy", "/terms", "list-manage.com",
    "signin", "sign-in", "/login", "help.", "support.",
    "facebook.com", "twitter.com", "instagram.com", "youtube.com",
    "apps.apple.com", "play.google.com",
)


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


def _is_junk(url: str) -> bool:
    lu = url.lower()
    if not lu.startswith("http"):
        return True
    return any(s in lu for s in JUNK_SUBSTRINGS)


def harvest_links(msg: email.message.Message) -> list[str]:
    """Pull candidate links from the email: prefer <a href> in the HTML part,
    fall back to bare URLs in the plain-text part. Dedup, preserve order,
    drop obvious junk."""
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
        if ctype == "text/html":
            html_parts.append(decoded)
        else:
            text_parts.append(decoded)

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
        if _is_junk(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


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
            results.append({
                "uid": uid.decode(),
                "subject": _decode(msg.get("Subject")),
                "sender": _decode(msg.get("From")),
                "date": _decode(msg.get("Date")),
                "links": harvest_links(msg),
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
