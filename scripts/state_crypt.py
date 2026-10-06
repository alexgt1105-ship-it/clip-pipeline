"""Pack the pipeline's runtime state into one encrypted file, and back.

The repo is public (unlimited Actions minutes), so the contract list and clip log never
sit in it as plain text. state.enc is AES-256 under STATE_KEY, which lives only in a
GitHub Secret and on the Mac.

  python scripts/state_crypt.py unpack            state.enc -> data/
  python scripts/state_crypt.py pack              data/ -> state.enc, only if it changed
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENC = ROOT / "state.enc"
FILES = ["data/watchlist.json", "data/clips.json", "data/telegram_offset.txt"]


def _openssl(args: list[str], data: bytes) -> bytes:
    return subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-md", "sha256", "-pbkdf2", "-iter", "200000", "-salt",
         "-pass", "env:STATE_KEY", *args],
        input=data, capture_output=True, check=True,
    ).stdout


def _decrypt() -> dict:
    if not ENC.exists():
        return {}
    return json.loads(_openssl(["-d"], ENC.read_bytes()))


def unpack() -> None:
    bundle = _decrypt()
    for rel, text in bundle.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"unpacked {len(bundle)} file(s)")


def pack() -> None:
    bundle = {rel: (ROOT / rel).read_text() for rel in FILES if (ROOT / rel).exists()}
    if not bundle:
        sys.exit("no state files found; refusing to overwrite state.enc with nothing")
    # Encryption is salted, so re-encrypting identical state still changes the bytes.
    # Comparing plaintext keeps unchanged runs from making a commit.
    if bundle == _decrypt():
        print("state unchanged")
        return
    ENC.write_bytes(_openssl([], json.dumps(bundle, sort_keys=True).encode()))
    print(f"packed {len(bundle)} file(s)")


if __name__ == "__main__":
    if not os.environ.get("STATE_KEY"):
        sys.exit("STATE_KEY is not set")
    {"pack": pack, "unpack": unpack}[sys.argv[1]]()
