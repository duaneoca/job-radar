"""
Re-encrypt all user_api_keys rows from one Fernet key to another.

Usage
-----
Run from inside the tracker-api container (or with the same DATABASE_URL env):

    # migrate from legacy SHA-256(SECRET_KEY) derivation to ENCRYPTION_KEY:
    python scripts/reencrypt_keys.py \\
        --old-key-type legacy \\
        --new-key "$ENCRYPTION_KEY"

    # rotate from one Fernet key to another:
    python scripts/reencrypt_keys.py \\
        --old-key "$ENCRYPTION_KEY_OLD" \\
        --new-key "$ENCRYPTION_KEY_NEW"

The script is idempotent: rows that already decrypt under the new key are left
unchanged (MultiFernet tries new key first, old key second).  Run with
--dry-run to preview without writing.
"""

import argparse
import base64
import hashlib
import os
import sys
from typing import Union

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from sqlalchemy import create_engine, text


# ── helpers ──────────────────────────────────────────────────


def _legacy_fernet(secret_key: str) -> Fernet:
    raw = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Re-encrypt user_api_keys rows")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument(
        "--old-key-type",
        choices=["legacy"],
        help="Use the legacy SHA-256(SECRET_KEY) derivation as the old key "
             "(reads SECRET_KEY from env)",
    )
    grp.add_argument("--old-key", help="Old Fernet key (base64url-encoded 32 bytes)")
    p.add_argument("--new-key", required=True, help="New Fernet key to encrypt with")
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


# ── main ─────────────────────────────────────────────────────


def main() -> None:
    args = _parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("DATABASE_URL env var is required")

    if args.old_key_type == "legacy":
        secret_key = os.environ.get("SECRET_KEY")
        if not secret_key:
            sys.exit("SECRET_KEY env var is required when --old-key-type=legacy")
        old_fernet: Union[Fernet, MultiFernet] = _legacy_fernet(secret_key)
    else:
        old_fernet = Fernet(args.old_key)

    new_fernet = Fernet(args.new_key)
    # Try new key first; fall back to old — so already-migrated rows are skipped
    multi = MultiFernet([new_fernet, old_fernet])

    engine = create_engine(database_url)
    updated = skipped = errors = 0

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, encrypted_key FROM user_api_keys ORDER BY id")
        ).fetchall()

        print(f"Found {len(rows)} rows to process")

        for i in range(0, len(rows), args.batch_size):
            batch = rows[i : i + args.batch_size]
            for row_id, ciphertext in batch:
                try:
                    plaintext = multi.decrypt(ciphertext.encode())
                except InvalidToken:
                    print(f"  ERROR: could not decrypt row {row_id} — skipping")
                    errors += 1
                    continue

                new_ciphertext = new_fernet.encrypt(plaintext).decode()

                # Check if the row was already using the new key
                try:
                    new_fernet.decrypt(ciphertext.encode())
                    # Decrypted fine with new key → already migrated
                    skipped += 1
                    continue
                except InvalidToken:
                    pass

                if not args.dry_run:
                    conn.execute(
                        text(
                            "UPDATE user_api_keys SET encrypted_key = :c WHERE id = :id"
                        ),
                        {"c": new_ciphertext, "id": row_id},
                    )
                updated += 1

            if not args.dry_run:
                conn.commit()

            print(
                f"  batch {i // args.batch_size + 1}: "
                f"{updated} updated, {skipped} already migrated, {errors} errors"
            )

    print(
        f"\nDone. {'(dry-run) ' if args.dry_run else ''}"
        f"updated={updated}, already_migrated={skipped}, errors={errors}"
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
