#!/usr/bin/env python
"""Generate an Ed25519 key pair for QWeather JWT authentication.

Upload the PUBLIC key to the QWeather console (Project Management →
credentials); it hands back a Credential ID (JWT "kid") and a Project ID
(JWT "sub"). Keep the private key local — secrets/ is gitignored.

    uv run python scripts/generate_qweather_key.py
"""

import argparse
import os
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="secrets", help="where to write the key pair")
    parser.add_argument("--name", default="qweather_ed25519", help="base filename")
    parser.add_argument("--force", action="store_true", help="overwrite an existing key")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path = out_dir / f"{args.name}_private.pem"
    public_path = out_dir / f"{args.name}_public.pem"

    if private_path.exists() and not args.force:
        print(f"refusing to overwrite {private_path} (pass --force if you really mean it)")
        return 1

    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    public_path.write_bytes(public_pem)

    print(public_pem.decode().strip())
    print()
    print(f"private key: {private_path} (mode 0600, keep it secret)")
    print(f"public key:  {public_path} — paste this into the QWeather console")
    print()
    print("then set in .env:")
    print(f"  QWEATHER_JWT_PRIVATE_KEY_FILE={private_path}")
    print("  QWEATHER_JWT_KID=<Credential ID from the console>")
    print("  QWEATHER_JWT_SUB=<Project ID from the console>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
