from __future__ import annotations

import asyncio
import sys

from app.bot.bot import run_bot
from app.single_instance import SingleInstanceLockError


def main() -> None:
    try:
        asyncio.run(run_bot())
    except SingleInstanceLockError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
