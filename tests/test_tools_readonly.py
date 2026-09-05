"""The bounding guarantee.

The README and the pitch both claim Moneta cannot write to the ledger, and that the
claim is structural rather than a policy the model is asked to follow. That is only
true while every tool in the registry is a read, so it is asserted here rather than
left as prose.
"""

from __future__ import annotations

import pytest

from moneta.agent import SUBMIT_FINDING, build_function_declarations
from moneta.engine import reconcile
from moneta.tools import (
    RECON_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    ToolContext,
    ToolError,
    ToolRegistry,
)

WRITE_VERBS = (
    "create", "update", "delete", "write", "post", "insert", "modify",
    "adjust", "set_", "patch", "remove", "transfer", "pay", "refund_order",
)


@pytest.fixture
def registry(clean_pair):
    return ToolRegistry(ToolContext(clean_pair, reconcile(clean_pair)))


class TestReadOnly:
    def test_no_tool_name_suggests_a_write(self, registry):
        offenders = [n for n in registry.names if n.startswith(WRITE_VERBS)]
        assert offenders == [], f"tool names imply mutation: {offenders}"

    def test_every_declared_tool_has_a_handler_and_vice_versa(self, registry):
        declared = {d["name"] for d in TOOL_DEFINITIONS + RECON_TOOL_DEFINITIONS}
        assert declared == set(registry.names)

    def test_calling_every_tool_leaves_the_dataset_unchanged(self, registry, clean_pair):
        before_vouchers = [v.to_rows() for v in clean_pair.vouchers]
        before_rows = [r.to_row() for r in clean_pair.settlement_rows]

        args = {
            "fetch_settlement": {"settlement_utr": "UTR0001"},
            "fetch_settlement_entries": {"settlement_utr": "UTR0001"},
            "fetch_payment": {"order_id": "order_1"},
            "fetch_refunds_for_order": {"order_id": "order_1"},
            "fetch_ledger_entries": {"order_id": "order_1"},
            "search_settlement_entries_by_amount": {"amount_paise": 10_000},
            "compute_fee_breakdown": {"settlement_utr": "UTR0001"},
            "list_settlement_cycles": {},
            "get_reconciliation_summary": {},
            "lookup_order_status": {"order_id": "order_1"},
            "list_exceptions": {},
        }
        assert set(args) == set(registry.names), "a tool was added without a call here"
        for name, kwargs in args.items():
            registry.call(name, kwargs)

        assert [v.to_rows() for v in clean_pair.vouchers] == before_vouchers
        assert [r.to_row() for r in clean_pair.settlement_rows] == before_rows

    def test_unknown_tool_is_rejected(self, registry):
        with pytest.raises(ToolError, match="unknown tool"):
            registry.call("update_ledger", {})


class TestToolBehaviour:
    def test_fee_check_recomputes_against_the_rate_card(self, registry):
        out = registry.call("fetch_payment", {"order_id": "order_1"})
        assert out["fee_check"]["fee_matches_rate_card"] is True
        assert out["fee_check"]["mdr_bps"] == 200

    def test_fee_breakdown_exposes_both_gst_methods(self, registry):
        """This difference is the rounding-drift diagnosis, computed in Python."""
        out = registry.call("compute_fee_breakdown", {"settlement_utr": "UTR0001"})
        assert "gst_computed_per_transaction" in out
        assert "gst_if_computed_on_aggregate_fee" in out
        assert "per_transaction_vs_aggregate_gst_difference" in out

    def test_amounts_carry_exact_paise_alongside_formatting(self, registry):
        out = registry.call("fetch_settlement", {"settlement_utr": "UTR0001"})
        assert out["gross_payments"]["paise"] == 10_000
        assert out["gross_payments"]["formatted"] == "₹100.00"

    def test_missing_order_raises_rather_than_returning_empty(self, registry):
        with pytest.raises(ToolError, match="no payment row"):
            registry.call("fetch_payment", {"order_id": "does_not_exist"})

    def test_lookup_order_status_reports_the_engine_verdict(self, registry):
        out = registry.call("lookup_order_status", {"order_id": "order_1"})
        assert out["reconciliation_result"][0]["matched"] is True


class TestToolSetSeparation:
    def test_investigation_agent_does_not_see_the_engines_verdict(self):
        """Handing the investigator the answer invites it to restate rather than investigate."""
        names = {d.name for d in build_function_declarations()}
        assert "lookup_order_status" not in names
        assert "get_reconciliation_summary" not in names
        assert "list_exceptions" not in names
        assert SUBMIT_FINDING["name"] in names

    def test_qa_layer_sees_both_tool_sets(self):
        names = {
            d.name for d in build_function_declarations(TOOL_DEFINITIONS + RECON_TOOL_DEFINITIONS)
        }
        assert "lookup_order_status" in names
        assert "fetch_settlement" in names
        assert SUBMIT_FINDING["name"] not in names, "Q&A answers in prose, it does not file findings"

    def test_submit_finding_allows_an_honest_non_answer(self):
        assert "UNRESOLVED" in SUBMIT_FINDING["input_schema"]["properties"]["classification"]["enum"]
