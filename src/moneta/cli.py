from __future__ import annotations

import argparse
import json
from pathlib import Path

from .generate import generate

DEFAULT_DATA_DIR = Path("data")


def _cmd_generate(args: argparse.Namespace) -> int:
    summary = generate(
        seed=args.seed, n_orders=args.orders, out_dir=Path(args.out), name=args.name
    )
    print(json.dumps(summary, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moneta", description="Settlement intelligence agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate a synthetic settlement + books dataset.")
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument("--orders", type=int, default=120)
    gen.add_argument("--out", default=str(DEFAULT_DATA_DIR))
    gen.add_argument("--name", default="dev")
    gen.set_defaults(func=_cmd_generate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
