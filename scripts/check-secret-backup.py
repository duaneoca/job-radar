#!/usr/bin/env python3
"""Export the cluster's Secrets for off-site storage, and check a stored copy is
still current.

The nightly pg_dump protects the data. This protects the one thing that makes
the data readable: every `user_api_keys.encrypted_key`, every Gmail refresh
token and every Slack bot token in those dumps is Fernet-encrypted with
ENCRYPTION_KEY. Restore a dump without it and the database loads perfectly while
every stored credential is permanently unreadable.

Of everything in tracker-api-secrets, ENCRYPTION_KEY is the only value that
cannot be reissued. SECRET_KEY costs one round of logouts; the OAuth pairs can
be re-copied from Google and Slack; AWS keys can be rotated in IAM.

    # Take a fresh export to store in a password manager
    scripts/check-secret-backup.py export -o ~/jobradar-secrets.json

    # Later: has the cluster drifted from what's stored?
    scripts/check-secret-backup.py check ~/jobradar-secrets.json

`check` prints key NAMES and a verdict only — never a value — so its output is
safe to paste into an issue or a chat. `export` obviously does write the real
values; it forces mode 600 and refuses to write inside this repository.

Restoring is a single command:

    kubectl apply -f ~/jobradar-secrets.json
"""

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_NAMESPACE = "jobradar-production"

# Kubernetes reissues these per pod; storing them is noise, and they would be
# wrong on a rebuilt cluster anyway.
_SKIP_TYPE_PREFIX = "kubernetes.io/service-account"


def _kubectl_secrets(namespace: str) -> dict:
    try:
        out = subprocess.run(
            ["kubectl", "get", "secret", "-n", namespace, "-o", "json"],
            capture_output=True, text=True, check=True,
        ).stdout
    except FileNotFoundError:
        sys.exit("kubectl not found on PATH")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"kubectl failed: {(exc.stderr or '').strip() or exc}")
    return json.loads(out)


def _flatten(doc) -> dict:
    """{(secret_name, key): value_bytes} from any shape we might be handed.

    Accepts `kubectl -o json` for one Secret or a List, and a plain
    {"KEY": "value"} map, so an older hand-rolled backup still compares.
    """
    out: dict = {}
    items = doc.get("items", [doc]) if isinstance(doc, dict) else []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("metadata", {}).get("name")
        if "data" in it or "stringData" in it:
            for k, v in (it.get("data") or {}).items():
                try:
                    out[(name, k)] = base64.b64decode(v)
                except Exception:
                    out[(name, k)] = str(v).encode()
            for k, v in (it.get("stringData") or {}).items():
                out[(name, k)] = str(v).encode()
        elif name is None:
            for k, v in it.items():
                if isinstance(v, str):
                    out[(None, k)] = v.encode()
    return out


def _named(flat: dict) -> bool:
    """Whether this side knows which Secret each key came from."""
    return any(secret is not None for secret, _key in flat)


def _by_key(flat: dict, qualified: bool) -> dict:
    """Collapse to {label: {values}} for comparison.

    Qualified (`tracker-api-secrets/ENCRYPTION_KEY`) whenever BOTH sides know
    their Secret names. A key present in one Secret does not vouch for the same
    key in another: AGENT_INTERNAL_TOKEN lives in two, and a backup missing it
    from tracker-api-secrets would restore that Secret broken while still
    "matching" on name alone.

    Falls back to bare names when the stored file is a flat {KEY: value} map,
    which has no Secret to qualify with."""
    grouped: dict = {}
    for (secret, key), value in flat.items():
        label = f"{secret}/{key}" if qualified and secret else key
        grouped.setdefault(label, set()).add(value)
    return grouped


def cmd_export(args) -> int:
    dest = Path(args.output).expanduser().resolve()

    repo = Path(__file__).resolve().parent.parent
    if repo in dest.parents or dest.parent == repo:
        sys.exit(f"refusing to write secrets inside the repository ({repo}).\n"
                 "Choose a path outside it — this file must never be committed.")

    doc = _kubectl_secrets(args.namespace)
    items = []
    for it in doc.get("items", []):
        if str(it.get("type", "")).startswith(_SKIP_TYPE_PREFIX):
            continue
        md = it.get("metadata", {})
        items.append({
            "apiVersion": "v1", "kind": "Secret", "type": it.get("type", "Opaque"),
            # Everything else (resourceVersion, uid, creationTimestamp,
            # managedFields) is cluster-instance state and would not apply to a
            # rebuilt cluster.
            "metadata": {"name": md.get("name"), "namespace": md.get("namespace")},
            "data": it.get("data", {}),
        })

    payload = json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2)
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload + "\n")
    os.chmod(dest, 0o600)

    total = sum(len(i["data"]) for i in items)
    print(f"wrote {dest} (mode 600)")
    for i in items:
        print(f"  {i['metadata']['name']:<24} {len(i['data'])} keys")
    print(f"  {len(items)} secrets, {total} keys")
    print("\nValues are base64, NOT encrypted — store it in a password manager and")
    print("delete the local copy. Restore with: kubectl apply -f <file>")
    return 0


def cmd_check(args) -> int:
    stored_path = Path(args.backup).expanduser()
    if not stored_path.exists():
        sys.exit(f"no such file: {stored_path}")
    try:
        stored_flat = _flatten(json.load(open(stored_path)))
    except json.JSONDecodeError as exc:
        sys.exit(f"{stored_path} is not JSON ({exc}). Re-export with: "
                 f"{Path(__file__).name} export -o <file>")

    live_flat = _flatten(_kubectl_secrets(args.namespace))
    qualified = _named(stored_flat) and _named(live_flat)
    stored = _by_key(stored_flat, qualified)
    live = _by_key(live_flat, qualified)
    if not qualified:
        print("note: the backup has no Secret names, so keys are compared by name\n"
              "      only. Re-export to get per-Secret checking.\n")

    verdict = {}
    for key in sorted(set(live) | set(stored)):
        if key not in stored:
            verdict[key] = "MISSING FROM BACKUP"
        elif key not in live:
            verdict[key] = "stale (not in cluster)"
        elif live[key] & stored[key]:
            verdict[key] = "match"
        else:
            verdict[key] = "DIFFERS"

    if not verdict:
        sys.exit("no keys found in either the cluster or the backup")

    width = max(len(k) for k in verdict)
    for key, result in verdict.items():
        marker = "  <--" if result in ("MISSING FROM BACKUP", "DIFFERS") else ""
        print(f"  {key:<{width}}  {result}{marker}")

    bad = [k for k, v in verdict.items() if v in ("MISSING FROM BACKUP", "DIFFERS")]
    print(f"\n{len(verdict)} keys checked · {len(bad)} need attention"
          + (f": {', '.join(bad)}" if bad else " — backup is current"))
    if any(k.endswith("ENCRYPTION_KEY") for k in bad):
        print("\nENCRYPTION_KEY is the one that matters: a backup holding the wrong\n"
              "value decrypts nothing, and a restored database would be full of\n"
              "unreadable API keys and mail credentials.")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-n", "--namespace", default=DEFAULT_NAMESPACE,
                        help=f"cluster namespace (default: {DEFAULT_NAMESPACE})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="write a restorable copy of every Secret")
    p_export.add_argument("-o", "--output", required=True, help="destination path (outside this repo)")
    p_export.set_defaults(func=cmd_export)

    p_check = sub.add_parser("check", help="compare a stored copy against the cluster")
    p_check.add_argument("backup", help="path to a previously exported file")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
