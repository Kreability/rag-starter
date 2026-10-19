"""Fill in the empty secret placeholders of an env file with fresh random values.

Run by `make init` right after copying a .template to a real .env file. Only
empty assignments (`KEY=`) are replaced, so anything the operator already
filled in is left alone and re-running is safe.

Usage: python3 scripts/gen_secrets.py .env.backend
"""

from __future__ import annotations

import base64
import re
import secrets
import sys
from pathlib import Path

# key -> how to generate its value. "b64:N" is N random bytes, base64 encoded;
# "hex:N" is N random bytes as hex. The byte counts match what each consumer
# actually requires, so nothing gets truncated or padded at runtime.
GENERATORS = {
    # Django signing key; the template comment asks for `openssl rand -base64 48`.
    "SECRET_KEY": "b64:36",
    # NextAuth/Auth.js session cookie.
    "NEXTAUTH_SECRET": "b64:24",
    # Langfuse encrypts every stored secret with this; the compose file has no
    # default for it and refuses to start without one, so a fresh .env has to
    # carry a real value or `make observability` fails on a missing var.
    "LANGFUSE_ENCRYPTION_KEY": "hex:32",
    # Langfuse's key-derivation salt. Rotating it invalidates existing
    # ciphertext, so it is generated once, here, and left alone afterwards.
    "LANGFUSE_SALT": "hex:32",
    # Signs Langfuse's own session cookies.
    "LANGFUSE_NEXTAUTH_SECRET": "hex:32",
}


def _generate(spec: str) -> str:
    kind, _, count = spec.partition(":")
    size = int(count)
    raw = secrets.token_bytes(size)
    if kind == "b64":
        return base64.b64encode(raw).decode()
    if kind == "hex":
        return raw.hex()
    raise ValueError(f"unknown generator spec: {spec!r}")


def fill(path: Path) -> list[str]:
    """Replace empty assignments for known keys. Returns the keys changed."""
    text = path.read_text()
    changed: list[str] = []

    for key, spec in GENERATORS.items():
        # `^KEY=$` (with the line ending) matches only a genuinely empty
        # assignment, so a placeholder comment or an operator-set value is
        # never clobbered.
        pattern = re.compile(rf"^{key}=$", re.MULTILINE)
        if not pattern.search(text):
            continue
        text = pattern.sub(f"{key}={_generate(spec)}", text)
        changed.append(key)

    if changed:
        path.write_text(text)
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1
    for key in fill(path):
        print(f"  generated {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
