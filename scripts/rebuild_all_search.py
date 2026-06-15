from __future__ import annotations

import argparse
import subprocess
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild all local search artifacts for an explicit database.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--allow-active-writer", action="store_true")
    parser.add_argument("--with-faiss", action="store_true", help="Also build the optional semantic FAISS index.")
    return parser.parse_args()


def run_step(args: list[str]) -> None:
    subprocess.run([sys.executable, *args], check=True)


def main() -> None:
    args = parse_args()
    common = ["--database-url", args.database_url]
    if args.allow_active_writer:
        common.append("--allow-active-writer")
    run_step(["scripts/rebuild_search_text.py", *common, "--batch-size", str(max(1, args.batch_size))])
    run_step(["scripts/rebuild_fts_index.py", *common])
    if args.with_faiss:
        run_step(["scripts/build_faiss_index.py", *common])
    run_step(["scripts/test_search.py", "--database-url", args.database_url])


if __name__ == "__main__":
    main()
