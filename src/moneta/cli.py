from __future__ import annotations

import argparse
import json
from pathlib import Path

from .generate import generate
from .agent import MODEL, resolve_api_key
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
        min_interval=args.min_interval,
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


def _cmd_models(args: argparse.Namespace) -> int:
    from google import genai

    key = resolve_api_key()
    if not key:
        print("No Gemini API key found. Set GEMINI_API_KEY or GOOGLE_API_KEY.")
        return 1
    client = genai.Client(api_key=key)
    rows = []
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        rows.append((m.name.replace("models/", ""), getattr(m, "display_name", "") or ""))
    for name, display in sorted(rows):
        marker = " <- default" if name == MODEL else ""
        print(f"  {name:42s} {display}{marker}")
    print(f"\n{len(rows)} models support generateContent")
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
    rec.add_argument("--model", default=None, help=f"Gemini model id (default: {MODEL}).")
    rec.add_argument("--min-interval", type=float, default=None, help="Seconds between API calls; free tier is rate limited.")
    rec.add_argument("--quiet", action="store_true")
    rec.set_defaults(func=_cmd_reconcile)

    mod = sub.add_parser("models", help="List Gemini models available to your API key.")
    mod.set_defaults(func=_cmd_models)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
