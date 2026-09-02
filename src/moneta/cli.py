from __future__ import annotations

import argparse
import json
from pathlib import Path

from .generate import generate
from .pipeline import run

DEFAULT_DATA_DIR = Path("data")


def _cmd_generate(args: argparse.Namespace) -> int:
    summary = generate(
        seed=args.seed, n_orders=args.orders, out_dir=Path(args.out), name=args.name
    )
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_reconcile(args: argparse.Namespace) -> int:
    def progress(i: int, total: int, case) -> None:
        print(f"  investigating case {i}/{total}: {case.key} ({case.family})", flush=True)

    result = run(
        Path(args.data_dir),
        args.name,
        use_agent=not args.no_agent,
        progress=None if args.quiet else progress,
        model=args.model,
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = result.audit.write_jsonl(out_dir / f"{args.name}.audit.jsonl")
    report_path = out_dir / f"{args.name}.report.json"
    report_path.write_text(json.dumps(result.report, indent=2, ensure_ascii=False), encoding="utf-8")
    findings_path = out_dir / f"{args.name}.findings.json"
    findings_path.write_text(
        json.dumps([f.to_dict() for f in result.findings], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(result.report, indent=2, ensure_ascii=False))
    print(f"\naudit trail : {audit_path}")
    print(f"report      : {report_path}")
    print(f"findings    : {findings_path}")
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

    rec = sub.add_parser("reconcile", help="Reconcile a dataset and investigate the residue.")
    rec.add_argument("--name", default="dev")
    rec.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    rec.add_argument("--out", default="out")
    rec.add_argument("--no-agent", action="store_true", help="Run the deterministic pass only.")
    rec.add_argument("--model", default=None)
    rec.add_argument("--quiet", action="store_true")
    rec.set_defaults(func=_cmd_reconcile)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
