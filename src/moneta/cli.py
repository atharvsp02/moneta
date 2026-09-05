from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .generate import generate
from .agent import MODEL, resolve_api_key
from .eval import evaluate, format_eval, write_eval
from .pipeline import RunResult, run

DEFAULT_DATA_DIR = Path("data")


def _progress(i: int, total: int, case) -> None:
    print(f"  investigating case {i}/{total}: {case.key} ({case.family})", flush=True)


def _write_artifacts(result: RunResult, out_dir: Path, name: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = result.audit.write_jsonl(out_dir / f"{name}.audit.jsonl")
    report_path = out_dir / f"{name}.report.json"
    report_path.write_text(
        json.dumps(result.report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    findings_path = out_dir / f"{name}.findings.json"
    findings_path.write_text(
        json.dumps([f.to_dict() for f in result.findings], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"audit": audit_path, "report": report_path, "findings": findings_path}


def _cmd_generate(args: argparse.Namespace) -> int:
    summary = generate(
        seed=args.seed, n_orders=args.orders, out_dir=Path(args.out), name=args.name
    )
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_reconcile(args: argparse.Namespace) -> int:
    result = run(
        Path(args.data_dir),
        args.name,
        use_agent=not args.no_agent,
        progress=None if args.quiet else _progress,
        model=args.model,
        min_interval=args.min_interval,
    )
    paths = _write_artifacts(result, Path(args.out), args.name)
    print(json.dumps(result.report, indent=2, ensure_ascii=False))
    print(f"\naudit trail : {paths['audit']}")
    print(f"report      : {paths['report']}")
    print(f"findings    : {paths['findings']}")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    result = run(
        Path(args.data_dir),
        args.name,
        use_agent=not args.no_agent,
        progress=None if args.quiet else _progress,
        model=args.model,
        min_interval=args.min_interval,
    )
    paths = _write_artifacts(result, Path(args.out), args.name)
    ev = evaluate(result, Path(args.data_dir), args.name)
    eval_path = write_eval(ev, Path(args.out), args.name)
    print(format_eval(ev))
    if not result.agent_ran and result.agent_error:
        print(f"\nNOTE: agent did not run — {result.agent_error}")
        print("Settlement-scope classes are the agent's to attribute, so they score as missed.")
    print(f"\naudit trail : {paths['audit']}")
    print(f"report      : {paths['report']}")
    print(f"eval        : {eval_path}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    # api.py reads its dataset location from the environment at import time, so these
    # have to be set before the app module is loaded.
    os.environ["MONETA_DATA_DIR"] = args.data_dir
    os.environ["MONETA_OUT_DIR"] = args.out
    os.environ["MONETA_DATASET"] = args.name
    print(f"serving dataset '{args.name}' on http://{args.host}:{args.port}")
    print(f"  docs at http://{args.host}:{args.port}/docs")
    uvicorn.run("moneta.api:app", host=args.host, port=args.port, reload=args.reload)
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

    ev = sub.add_parser(
        "eval", help="Reconcile a dataset and score the result against its injected ground truth."
    )
    ev.add_argument("--name", default="holdout")
    ev.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ev.add_argument("--out", default="out")
    ev.add_argument("--no-agent", action="store_true", help="Score the deterministic pass only.")
    ev.add_argument("--model", default=None, help=f"Gemini model id (default: {MODEL}).")
    ev.add_argument("--min-interval", type=float, default=None)
    ev.add_argument("--quiet", action="store_true")
    ev.set_defaults(func=_cmd_eval)

    srv = sub.add_parser("serve", help="Serve the reconciliation API for the dashboard.")
    srv.add_argument("--name", default="dev")
    srv.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    srv.add_argument("--out", default="out")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument("--reload", action="store_true")
    srv.set_defaults(func=_cmd_serve)

    mod = sub.add_parser("models", help="List Gemini models available to your API key.")
    mod.set_defaults(func=_cmd_models)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Amounts are printed with the rupee sign; the default Windows console codec is
    # cp1252 and cannot encode it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
