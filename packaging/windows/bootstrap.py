"""Prepare a Windows installation before the API service starts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from api.security.crypto import generate_key


def main() -> None:
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    contents = env_path.read_text(encoding="utf-8")
    if "ENCRYPTION_KEY=\n" in contents:
        env_path.write_text(
            contents.replace("ENCRYPTION_KEY=\n", f"ENCRYPTION_KEY={generate_key()}\n"),
            encoding="utf-8",
        )
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, check=True)


if __name__ == "__main__":
    main()
