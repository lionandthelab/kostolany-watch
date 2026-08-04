#!/usr/bin/env python3
"""Generate VAPID keys for Web Push and print / append to .env."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    v = Vapid01()
    v.generate_keys()
    assert v.public_key is not None and v.private_key is not None
    raw_pub = v.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public = base64.urlsafe_b64encode(raw_pub).decode("ascii").rstrip("=")
    private_pem = v.private_pem()
    if isinstance(private_pem, bytes):
        private_pem = private_pem.decode("utf-8")
    private_b64 = base64.urlsafe_b64encode(private_pem.encode("utf-8")).decode("ascii").rstrip("=")

    print("VAPID_PUBLIC_KEY=" + public)
    print("VAPID_PRIVATE_KEY=" + private_b64)
    print("VAPID_MAILTO=mailto:ops@lionandthelab.com")

    env_path = ROOT / ".env"
    if "--write-env" in sys.argv and env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        lines = [
            ln
            for ln in text.splitlines()
            if not ln.startswith("VAPID_PUBLIC_KEY=")
            and not ln.startswith("VAPID_PRIVATE_KEY=")
            and not ln.startswith("VAPID_MAILTO=")
        ]
        lines.append(f"VAPID_PUBLIC_KEY={public}")
        lines.append(f"VAPID_PRIVATE_KEY={private_b64}")
        lines.append("VAPID_MAILTO=mailto:ops@lionandthelab.com")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote keys to {env_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
