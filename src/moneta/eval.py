"""Scores a Moneta run against the injected ground truth in `<name>.labels.json`.

The generator records exactly which errors it injected and what each one cost. This
module replays a full pipeline run against that ledger of injected faults and reports
how many were caught, how many were attributed to the right root cause, and — the part
that matters most for an agent that talks about money — how many things Moneta claimed
that were not actually there.

Matching a label to a prediction is not purely by key. A cross-cycle refund is injected
against an `order_id` but surfaces as a bank-credit delta on a `settlement_utr`, so a
strict key join would score a correct attribution as a miss. The passes below resolve
key matches first and only then allow a cross-scope match on the classification itself,
so a prediction can never be claimed by more than one label.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .load import load_labels
from .money import rupees
from .pipeline import RunResult

CORRECT = "correct"
MISCLASSIFIED = "misclassified"
MISSED = "missed"


@dataclass
class Prediction:
    key: str
    classification: str
    source: str
    delta_paise: int
    confidence: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "classification": self.classification,
            "source": self.source,
            "delta": rupees(self.delta_paise),
            "confidence": self.confidence,
        }


@dataclass
class Outcome:
    label_key: str
    expected: str
    predicted: str | None
    status: str
    source: str | None
    matched_on: str | None
    expected_impact_paise: int
    predicted_delta_paise: int | None

    def to_dict(self) -> dict:
        return {
            "key": self.label_key,
            "expected": self.expected,
            "predicted": self.predicted,
            "status": self.status,
            "source": self.source,
            "matched_on": self.matched_on,
            "expected_impact": rupees(self.expected_impact_paise),
            "predicted_delta": (
                rupees(self.predicted_delta_paise)
                if self.predicted_delta_paise is not None
                else None
            ),
        }


@dataclass
class EvalResult:
    dataset: str
    run_id: str
    outcomes: list[Outcome] = field(default_factory=list)
    false_positives: list[Prediction] = field(default_factory=list)
    per_class: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)


def build_predictions(result: RunResult) -> list[Prediction]:
    """Every root-cause claim the run made, from either layer.

    Discrepancies the rules engine left open carry `classification is None` — they are
    handed to the agent instead, so they are not claims and are not counted here.
    """
    predictions: list[Prediction] = []
    for d in result.recon.discrepancies:
        if not d.classification:
            continue
        predictions.append(
            Prediction(
                key=d.key,
                classification=d.classification,
                source="rules",
                delta_paise=d.delta_paise,
                confidence="deterministic",
                detail=d.rule,
            )
        )
    for f in result.findings:
        predictions.append(
            Prediction(
                key=f.case_key,
                classification=f.classification,
                source="agent",
                delta_paise=f.delta_paise,
                confidence=f.confidence,
                detail=f.family,
            )
        )
    return predictions


def _match(labels: list[dict], predictions: list[Prediction]):
    """Bipartite match labels to predictions; each prediction is claimed at most once."""
    outcomes: list[Outcome] = []
    open_labels = list(labels)
    open_preds = list(predictions)

    def claim(label: dict, pred: Prediction, matched_on: str) -> None:
        outcomes.append(
            Outcome(
                label_key=label["order_id"],
                expected=label["error_type"],
                predicted=pred.classification,
                status=CORRECT if pred.classification == label["error_type"] else MISCLASSIFIED,
                source=pred.source,
                matched_on=matched_on,
                expected_impact_paise=label["impact_paise"],
                predicted_delta_paise=pred.delta_paise,
            )
        )
        open_preds.remove(pred)

    # Pass 1 — same key and same root cause. The unambiguous case.
    for label in list(open_labels):
        hit = next(
            (
                p
                for p in open_preds
                if p.key == label["order_id"] and p.classification == label["error_type"]
            ),
            None,
        )
        if hit:
            claim(label, hit, "key+class")
            open_labels.remove(label)

    # Pass 2 — same key, different root cause. Detected, but attributed wrongly.
    for label in list(open_labels):
        hit = next((p for p in open_preds if p.key == label["order_id"]), None)
        if hit:
            claim(label, hit, "key")
            open_labels.remove(label)

    # Pass 3 — cross-scope. The fault was injected against an order but surfaces on a
    # settlement, so accept a match on the root cause alone.
    for label in list(open_labels):
        hit = next((p for p in open_preds if p.classification == label["error_type"]), None)
        if hit:
            claim(label, hit, "class")
            open_labels.remove(label)

    for label in open_labels:
        outcomes.append(
            Outcome(
                label_key=label["order_id"],
                expected=label["error_type"],
                predicted=None,
                status=MISSED,
                source=None,
                matched_on=None,
                expected_impact_paise=label["impact_paise"],
                predicted_delta_paise=None,
            )
        )
    return outcomes, open_preds


def _per_class(outcomes: list[Outcome], false_positives: list[Prediction]) -> dict:
    classes = {o.expected for o in outcomes} | {p.classification for p in false_positives}
    table: dict[str, dict] = {}
    for cls in sorted(classes):
        expected = [o for o in outcomes if o.expected == cls]
        tp = sum(1 for o in expected if o.status == CORRECT)
        fn = len(expected) - tp
        # A prediction of this class is a false positive if it was never claimed by a
        # label, or if it was claimed by a label of a different class.
        fp = sum(1 for p in false_positives if p.classification == cls)
        fp += sum(1 for o in outcomes if o.predicted == cls and o.expected != cls)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall
            else (0.0 if precision is not None and recall is not None else None)
        )
        table[cls] = {
            "support": len(expected),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
        }
    return table


def evaluate(result: RunResult, data_dir: Path, name: str) -> EvalResult:
    labels = load_labels(Path(data_dir), name)
    predictions = build_predictions(result)
    outcomes, unclaimed = _match(labels, predictions)
    per_class = _per_class(outcomes, unclaimed)

    correct = [o for o in outcomes if o.status == CORRECT]
    detected = [o for o in outcomes if o.status in (CORRECT, MISCLASSIFIED)]
    unresolved = [o for o in outcomes if o.predicted == "UNRESOLVED"]
    total = len(outcomes)

    micro_tp = len(correct)
    micro_fp = len(unclaimed) + sum(1 for o in outcomes if o.status == MISCLASSIFIED)
    micro_fn = total - micro_tp
    scored = [c for c in per_class.values() if c["precision"] is not None]
    recalled = [c for c in per_class.values() if c["recall"] is not None]

    ev = EvalResult(dataset=name, run_id=result.run_id, outcomes=outcomes)
    ev.false_positives = unclaimed
    ev.per_class = per_class
    ev.totals = {
        "injected_errors": total,
        "detected": len(detected),
        "correctly_attributed": micro_tp,
        "misclassified": sum(1 for o in outcomes if o.status == MISCLASSIFIED),
        "missed": sum(1 for o in outcomes if o.status == MISSED),
        "honestly_unresolved": len(unresolved),
        "unclaimed_predictions": len(unclaimed),
        "detection_recall": round(len(detected) / total, 4) if total else None,
        "attribution_accuracy": round(micro_tp / total, 4) if total else None,
        "micro_precision": (
            round(micro_tp / (micro_tp + micro_fp), 4) if (micro_tp + micro_fp) else None
        ),
        "micro_recall": (
            round(micro_tp / (micro_tp + micro_fn), 4) if (micro_tp + micro_fn) else None
        ),
        "macro_precision": (
            round(sum(c["precision"] for c in scored) / len(scored), 4) if scored else None
        ),
        "macro_recall": (
            round(sum(c["recall"] for c in recalled) / len(recalled), 4) if recalled else None
        ),
    }

    by_source = {}
    for src in ("rules", "agent"):
        rows = [o for o in outcomes if o.source == src]
        by_source[src] = {
            "attributed": len(rows),
            "correct": sum(1 for o in rows if o.status == CORRECT),
            "accuracy": (
                round(sum(1 for o in rows if o.status == CORRECT) / len(rows), 4) if rows else None
            ),
        }

    ev.report = {
        "dataset": name,
        "run_id": result.run_id,
        "agent_ran": result.agent_ran,
        "agent_error": result.agent_error,
        "match_rate": result.report["match_rate"],
        "totals": ev.totals,
        "by_layer": by_source,
        "per_class": per_class,
        "misses": [o.to_dict() for o in outcomes if o.status == MISSED],
        "misclassifications": [o.to_dict() for o in outcomes if o.status == MISCLASSIFIED],
        "unclaimed_predictions": [p.to_dict() for p in unclaimed],
        "outcomes": [o.to_dict() for o in outcomes],
    }
    return ev


def write_eval(ev: EvalResult, out_dir: Path, name: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.eval.json"
    path.write_text(json.dumps(ev.report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def format_eval(ev: EvalResult) -> str:
    t = ev.totals
    lines = [
        f"Evaluation — dataset '{ev.dataset}' (run {ev.run_id})",
        "",
        f"  injected errors        {t['injected_errors']}",
        f"  detected               {t['detected']}  ({_pct(t['detection_recall'])})",
        f"  correctly attributed   {t['correctly_attributed']}  ({_pct(t['attribution_accuracy'])})",
        f"  misclassified          {t['misclassified']}",
        f"  missed entirely        {t['missed']}",
        f"  honestly UNRESOLVED    {t['honestly_unresolved']}",
        f"  unclaimed predictions  {t['unclaimed_predictions']}  (flagged, no injected fault behind it)",
        "",
        f"  micro precision {_pct(t['micro_precision'])}   micro recall {_pct(t['micro_recall'])}",
        f"  macro precision {_pct(t['macro_precision'])}   macro recall {_pct(t['macro_recall'])}",
        "",
        "  per class:",
        f"    {'classification':<28}{'n':>4}{'prec':>8}{'rec':>8}{'f1':>8}",
    ]
    for cls, row in ev.per_class.items():
        lines.append(
            f"    {cls:<28}{row['support']:>4}{_pct(row['precision']):>8}"
            f"{_pct(row['recall']):>8}{_pct(row['f1']):>8}"
        )
    if ev.report["misses"]:
        lines.append("")
        lines.append("  missed:")
        for m in ev.report["misses"]:
            lines.append(f"    {m['key']}  expected {m['expected']}  impact {m['expected_impact']}")
    if ev.report["misclassifications"]:
        lines.append("")
        lines.append("  misclassified:")
        for m in ev.report["misclassifications"]:
            lines.append(f"    {m['key']}  expected {m['expected']}  got {m['predicted']}")
    return "\n".join(lines)


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
