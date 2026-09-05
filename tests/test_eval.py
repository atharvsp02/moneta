"""Scoring against ground truth.

The three-pass matcher is where a scoring harness most easily flatters itself. The
tests that matter are the ones proving it cannot: a prediction may be claimed by only
one label, a wrong attribution counts against precision, and an unclaimed prediction
is a false positive rather than being quietly dropped.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moneta.eval import _match, _per_class, build_predictions


def label(key: str, error_type: str, impact: int = 1000) -> dict:
    return {"order_id": key, "error_type": error_type, "impact_paise": impact, "side": "books"}


def pred(key: str, classification: str, source: str = "rules", delta: int = 1000):
    from moneta.eval import Prediction

    return Prediction(
        key=key, classification=classification, source=source, delta_paise=delta, confidence="high"
    )


class TestMatching:
    def test_exact_key_and_class_is_correct(self):
        outcomes, unclaimed = _match([label("o1", "AMOUNT_MISMATCH")], [pred("o1", "AMOUNT_MISMATCH")])
        assert outcomes[0].status == "correct"
        assert outcomes[0].matched_on == "key+class"
        assert unclaimed == []

    def test_same_key_wrong_class_is_misclassified(self):
        outcomes, unclaimed = _match(
            [label("o1", "AMOUNT_MISMATCH")], [pred("o1", "DUPLICATE_BOOKING")]
        )
        assert outcomes[0].status == "misclassified"
        assert outcomes[0].matched_on == "key"
        assert unclaimed == []

    def test_cross_scope_matches_on_class_alone(self):
        """A cross-cycle refund is labelled against an order but surfaces on a UTR."""
        outcomes, _ = _match(
            [label("order_x", "CROSS_CYCLE_REFUND")], [pred("UTR999", "CROSS_CYCLE_REFUND")]
        )
        assert outcomes[0].status == "correct"
        assert outcomes[0].matched_on == "class"

    def test_unmatched_label_is_missed(self):
        outcomes, unclaimed = _match([label("o1", "AMOUNT_MISMATCH")], [])
        assert outcomes[0].status == "missed"
        assert outcomes[0].predicted is None

    def test_unmatched_prediction_is_a_false_positive(self):
        outcomes, unclaimed = _match([], [pred("o9", "AMOUNT_MISMATCH")])
        assert outcomes == []
        assert len(unclaimed) == 1

    def test_a_prediction_can_only_be_claimed_once(self):
        """Two labels of the same class must not both score against one prediction."""
        labels = [label("o1", "AMOUNT_MISMATCH"), label("o2", "AMOUNT_MISMATCH")]
        outcomes, unclaimed = _match(labels, [pred("o1", "AMOUNT_MISMATCH")])
        statuses = sorted(o.status for o in outcomes)
        assert statuses == ["correct", "missed"]
        assert unclaimed == []

    def test_exact_match_wins_over_cross_scope(self):
        """Pass ordering: o1's own prediction must not be stolen by a class-only match."""
        labels = [label("o1", "CROSS_CYCLE_REFUND")]
        preds = [pred("UTR999", "CROSS_CYCLE_REFUND"), pred("o1", "CROSS_CYCLE_REFUND")]
        outcomes, unclaimed = _match(labels, preds)
        assert outcomes[0].matched_on == "key+class"
        assert unclaimed[0].key == "UTR999"


class TestPerClassMetrics:
    def test_perfect_class_scores_one(self):
        outcomes, unclaimed = _match([label("o1", "AMOUNT_MISMATCH")], [pred("o1", "AMOUNT_MISMATCH")])
        table = _per_class(outcomes, unclaimed)
        assert table["AMOUNT_MISMATCH"] == {
            "support": 1,
            "true_positives": 1,
            "false_positives": 0,
            "false_negatives": 1 - 1,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
        }

    def test_misclassification_hurts_both_classes(self):
        outcomes, unclaimed = _match(
            [label("o1", "AMOUNT_MISMATCH")], [pred("o1", "DUPLICATE_BOOKING")]
        )
        table = _per_class(outcomes, unclaimed)
        # The true class loses recall...
        assert table["AMOUNT_MISMATCH"]["recall"] == 0.0
        # ...and the wrongly-claimed class loses precision.
        assert table["DUPLICATE_BOOKING"]["false_positives"] == 1

    def test_unclaimed_prediction_lowers_precision(self):
        outcomes, unclaimed = _match([], [pred("o9", "AMOUNT_MISMATCH")])
        table = _per_class(outcomes, unclaimed)
        assert table["AMOUNT_MISMATCH"]["precision"] == 0.0
        assert table["AMOUNT_MISMATCH"]["false_positives"] == 1

    def test_a_class_only_ever_claimed_wrongly_still_gets_a_row(self):
        """Regression: such a class was absent from the table, hiding its false positive."""
        outcomes, unclaimed = _match(
            [label("o1", "AMOUNT_MISMATCH")], [pred("o1", "DUPLICATE_BOOKING")]
        )
        table = _per_class(outcomes, unclaimed)
        assert "DUPLICATE_BOOKING" in table
        assert table["DUPLICATE_BOOKING"]["support"] == 0
        assert table["DUPLICATE_BOOKING"]["precision"] == 0.0


class TestHonestNonAnswer:
    """UNRESOLVED must cost recall but never count as a wrong claim.

    The system is explicitly built to prefer an honest non-answer over a plausible
    wrong attribution. A metric that penalises both identically would reward guessing.
    """

    def test_unresolved_is_not_a_scored_class(self):
        outcomes, unclaimed = _match([label("o1", "AMOUNT_MISMATCH")], [pred("o1", "UNRESOLVED")])
        assert "UNRESOLVED" not in _per_class(outcomes, unclaimed)

    def test_unresolved_still_costs_the_true_class_its_recall(self):
        outcomes, unclaimed = _match([label("o1", "AMOUNT_MISMATCH")], [pred("o1", "UNRESOLVED")])
        table = _per_class(outcomes, unclaimed)
        assert table["AMOUNT_MISMATCH"]["recall"] == 0.0
        assert table["AMOUNT_MISMATCH"]["false_negatives"] == 1

    def test_guessing_wrongly_is_punished_harder_than_admitting_ignorance(self):
        honest, honest_unclaimed = _match(
            [label("o1", "AMOUNT_MISMATCH")], [pred("o1", "UNRESOLVED")]
        )
        guess, guess_unclaimed = _match(
            [label("o1", "AMOUNT_MISMATCH")], [pred("o1", "DUPLICATE_BOOKING")]
        )
        honest_fps = sum(c["false_positives"] for c in _per_class(honest, honest_unclaimed).values())
        guess_fps = sum(c["false_positives"] for c in _per_class(guess, guess_unclaimed).values())
        assert honest_fps == 0
        assert guess_fps == 1


class TestBuildPredictions:
    def test_open_discrepancies_are_not_claims(self):
        """A case handed to the agent has not been attributed, so it must not score."""
        recon = SimpleNamespace(
            discrepancies=[
                SimpleNamespace(key="a", classification=None, delta_paise=10, rule="r"),
                SimpleNamespace(key="b", classification="AMOUNT_MISMATCH", delta_paise=20, rule="r"),
            ]
        )
        result = SimpleNamespace(recon=recon, findings=[])
        preds = build_predictions(result)
        assert [p.key for p in preds] == ["b"]

    def test_agent_findings_are_attributed_to_the_agent(self):
        recon = SimpleNamespace(discrepancies=[])
        finding = SimpleNamespace(
            case_key="UTR1", classification="CROSS_CYCLE_REFUND", delta_paise=99,
            confidence="high", family="bank_credit",
        )
        preds = build_predictions(SimpleNamespace(recon=recon, findings=[finding]))
        assert preds[0].source == "agent"
        assert preds[0].confidence == "high"
