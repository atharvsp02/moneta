from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .engine import Reconciliation
from .money import bps_of, gst_on, rupees
from .schema import MDR_BPS, Account, Dataset, EntryType, Method, VoucherType


class ToolError(Exception):
    pass


def _money(paise: int) -> dict:
    return {"paise": paise, "formatted": rupees(paise)}


def _row_view(row) -> dict:
    return {
        "entity_id": row.entity_id,
        "type": row.type.value,
        "order_id": row.order_id,
        "method": row.method.value,
        "amount": _money(row.amount),
        "fee": _money(row.fee),
        "tax": _money(row.tax),
        "net_effect": _money(row.net),
        "settled": row.settled,
        "settled_at": row.settled_at.isoformat() if row.settled_at else None,
        "settlement_id": row.settlement_id,
        "settlement_utr": row.settlement_utr,
        "created_at": row.created_at.isoformat(),
    }


def _voucher_view(v) -> dict:
    return {
        "voucher_id": v.voucher_id,
        "voucher_type": v.voucher_type.value,
        "voucher_date": v.voucher_date.isoformat(),
        "narration": v.narration,
        "order_id": v.order_id,
        "settlement_utr": v.settlement_utr,
        "lines": [
            {"account": l.account.value, "debit": _money(l.debit), "credit": _money(l.credit)}
            for l in v.lines
        ],
    }


@dataclass
class ToolContext:
    dataset: Dataset
    recon: Reconciliation


class ToolRegistry:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._handlers = {
            "fetch_settlement": self.fetch_settlement,
            "fetch_settlement_entries": self.fetch_settlement_entries,
            "fetch_payment": self.fetch_payment,
            "fetch_refunds_for_order": self.fetch_refunds_for_order,
            "fetch_ledger_entries": self.fetch_ledger_entries,
            "search_settlement_entries_by_amount": self.search_settlement_entries_by_amount,
            "compute_fee_breakdown": self.compute_fee_breakdown,
            "list_settlement_cycles": self.list_settlement_cycles,
        }

    @property
    def names(self) -> list[str]:
        return list(self._handlers)

    def call(self, name: str, args: dict) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        return handler(**args)

    def _settlement(self, settlement_utr: str):
        for s in self.ctx.dataset.settlements:
            if s.settlement_utr == settlement_utr or s.settlement_id == settlement_utr:
                return s
        raise ToolError(f"no settlement found for '{settlement_utr}'")

    def fetch_settlement(self, settlement_utr: str) -> dict:
        stl = self._settlement(settlement_utr)
        rows = [r for r in self.ctx.dataset.settlement_rows if r.settlement_id == stl.settlement_id]
        gross = sum(r.amount for r in rows if r.type is EntryType.PAYMENT)
        fee = sum(r.fee for r in rows)
        tax = sum(r.tax for r in rows)
        refunds = sum(r.amount for r in rows if r.type is EntryType.REFUND)
        receipt = next(
            (
                v
                for v in self.ctx.dataset.vouchers
                if v.voucher_type is VoucherType.RECEIPT and v.settlement_utr == stl.settlement_utr
            ),
            None,
        )
        return {
            "settlement_id": stl.settlement_id,
            "settlement_utr": stl.settlement_utr,
            "settled_at": stl.settled_at.isoformat(),
            "payment_count": sum(1 for r in rows if r.type is EntryType.PAYMENT),
            "refund_count": sum(1 for r in rows if r.type is EntryType.REFUND),
            "gross_payments": _money(gross),
            "total_fee": _money(fee),
            "total_gst_on_fee": _money(tax),
            "total_refunds_netted": _money(refunds),
            "reconstructed_net_credit": _money(gross - fee - tax - refunds),
            "books_receipt_voucher": _voucher_view(receipt) if receipt else None,
        }

    def fetch_settlement_entries(
        self, settlement_utr: str, entry_type: str | None = None, limit: int = 50
    ) -> dict:
        stl = self._settlement(settlement_utr)
        rows = [r for r in self.ctx.dataset.settlement_rows if r.settlement_id == stl.settlement_id]
        if entry_type:
            rows = [r for r in rows if r.type.value == entry_type]
        return {
            "settlement_utr": stl.settlement_utr,
            "total_matching": len(rows),
            "returned": min(len(rows), limit),
            "entries": [_row_view(r) for r in rows[:limit]],
        }

    def fetch_payment(self, order_id: str) -> dict:
        rows = [
            r
            for r in self.ctx.dataset.settlement_rows
            if r.order_id == order_id and r.type is EntryType.PAYMENT
        ]
        if not rows:
            raise ToolError(f"no payment row found for order_id '{order_id}'")
        row = rows[0]
        expected_fee = bps_of(row.amount, MDR_BPS[row.method])
        return {
            "payment": _row_view(row),
            "fee_check": {
                "method": row.method.value,
                "mdr_bps": MDR_BPS[row.method],
                "expected_fee": _money(expected_fee),
                "actual_fee": _money(row.fee),
                "expected_gst_on_fee": _money(gst_on(expected_fee)),
                "actual_gst_on_fee": _money(row.tax),
                "fee_matches_rate_card": expected_fee == row.fee and gst_on(expected_fee) == row.tax,
            },
        }

    def fetch_refunds_for_order(self, order_id: str) -> dict:
        rows = [
            r
            for r in self.ctx.dataset.settlement_rows
            if r.order_id == order_id and r.type is EntryType.REFUND
        ]
        notes = [
            v
            for v in self.ctx.dataset.vouchers
            if v.voucher_type is VoucherType.CREDIT_NOTE and v.order_id == order_id
        ]
        return {
            "order_id": order_id,
            "refund_rows_in_settlement_data": [_row_view(r) for r in rows],
            "credit_notes_in_books": [_voucher_view(v) for v in notes],
            "note": (
                "A refund netted by Razorpay in one settlement cycle may be recorded in the books "
                "at the date it was issued, which can fall in a different cycle."
            ),
        }

    def fetch_ledger_entries(
        self, order_id: str | None = None, settlement_utr: str | None = None
    ) -> dict:
        if not order_id and not settlement_utr:
            raise ToolError("provide either order_id or settlement_utr")
        vouchers = [
            v
            for v in self.ctx.dataset.vouchers
            if (order_id and v.order_id == order_id)
            or (settlement_utr and v.settlement_utr == settlement_utr)
        ]
        return {
            "query": {"order_id": order_id, "settlement_utr": settlement_utr},
            "voucher_count": len(vouchers),
            "vouchers": [_voucher_view(v) for v in vouchers],
        }

    def search_settlement_entries_by_amount(
        self, amount_paise: int, tolerance_paise: int = 0, entry_type: str | None = None
    ) -> dict:
        matches = []
        for r in self.ctx.dataset.settlement_rows:
            if entry_type and r.type.value != entry_type:
                continue
            if abs(r.amount - amount_paise) <= tolerance_paise:
                matches.append(r)
        return {
            "query": {
                "amount": _money(amount_paise),
                "tolerance": _money(tolerance_paise),
                "entry_type": entry_type,
            },
            "match_count": len(matches),
            "matches": [_row_view(r) for r in matches[:25]],
        }

    def compute_fee_breakdown(self, settlement_utr: str) -> dict:
        stl = self._settlement(settlement_utr)
        rows = [
            r
            for r in self.ctx.dataset.settlement_rows
            if r.settlement_id == stl.settlement_id and r.type is EntryType.PAYMENT
        ]
        buckets: dict[str, dict] = defaultdict(
            lambda: {"count": 0, "gross_paise": 0, "fee_paise": 0, "tax_paise": 0}
        )
        for r in rows:
            b = buckets[r.method.value]
            b["count"] += 1
            b["gross_paise"] += r.amount
            b["fee_paise"] += r.fee
            b["tax_paise"] += r.tax
        gross = sum(b["gross_paise"] for b in buckets.values())
        fee = sum(b["fee_paise"] for b in buckets.values())
        tax = sum(b["tax_paise"] for b in buckets.values())
        by_method = []
        for method, b in sorted(buckets.items()):
            by_method.append(
                {
                    "method": method,
                    "mdr_bps": MDR_BPS[Method(method)],
                    "payment_count": b["count"],
                    "gross": _money(b["gross_paise"]),
                    "fee": _money(b["fee_paise"]),
                    "gst_on_fee": _money(b["tax_paise"]),
                    "share_of_gross_pct": round(100 * b["gross_paise"] / gross, 2) if gross else 0.0,
                }
            )
        return {
            "settlement_utr": stl.settlement_utr,
            "gross": _money(gross),
            "total_fee": _money(fee),
            "total_gst_on_fee": _money(tax),
            "blended_mdr_bps": round(10_000 * fee / gross, 2) if gross else 0.0,
            "gst_computed_per_transaction": _money(tax),
            "gst_if_computed_on_aggregate_fee": _money(gst_on(fee)),
            "per_transaction_vs_aggregate_gst_difference": _money(tax - gst_on(fee)),
            "by_method": by_method,
        }

    def list_settlement_cycles(self) -> dict:
        out = []
        for s in sorted(self.ctx.dataset.settlements, key=lambda s: s.settled_at):
            rows = [r for r in self.ctx.dataset.settlement_rows if r.settlement_id == s.settlement_id]
            out.append(
                {
                    "settlement_utr": s.settlement_utr,
                    "settled_at": s.settled_at.isoformat(),
                    "payment_count": sum(1 for r in rows if r.type is EntryType.PAYMENT),
                    "refund_count": sum(1 for r in rows if r.type is EntryType.REFUND),
                    "net_credit": _money(s.net_amount),
                }
            )
        return {"cycle_count": len(out), "cycles": out}


TOOL_DEFINITIONS = [
    {
        "name": "fetch_settlement",
        "description": (
            "Fetch one Razorpay settlement by UTR or settlement id, with its reconstructed net "
            "credit (gross payments minus fee minus GST on fee minus refunds netted) and the "
            "merchant's receipt voucher for that settlement if one exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"settlement_utr": {"type": "string"}},
            "required": ["settlement_utr"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "fetch_settlement_entries",
        "description": "List the individual payment and refund line items inside one settlement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "settlement_utr": {"type": "string"},
                "entry_type": {"type": "string", "enum": ["payment", "refund", "adjustment"]},
                "limit": {"type": "integer"},
            },
            "required": ["settlement_utr"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fetch_payment",
        "description": (
            "Fetch a single payment by order_id, including a rate-card check that recomputes the "
            "expected MDR and GST for that payment method in Python."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "fetch_refunds_for_order",
        "description": (
            "Fetch every refund row Razorpay recorded for an order across all settlement cycles, "
            "alongside every credit note in the merchant's books for that order. Use this to test "
            "whether a refund was recorded in a different cycle than the one it settled in."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "fetch_ledger_entries",
        "description": "Fetch the merchant's book vouchers for an order_id or a settlement UTR.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "settlement_utr": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_settlement_entries_by_amount",
        "description": (
            "Search every settlement cycle for entries matching an amount within a tolerance. Use "
            "this to locate where an unexplained value went, for example a refund that landed in a "
            "later cycle than the books expected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_paise": {"type": "integer"},
                "tolerance_paise": {"type": "integer"},
                "entry_type": {"type": "string", "enum": ["payment", "refund", "adjustment"]},
            },
            "required": ["amount_paise"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compute_fee_breakdown",
        "description": (
            "Compute, in Python, the per-method fee and GST breakdown for one settlement: gross and "
            "fee per payment method, the blended MDR in basis points, and what the GST would have "
            "been if computed on the aggregate fee instead of per transaction. Use this instead of "
            "doing arithmetic yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"settlement_utr": {"type": "string"}},
            "required": ["settlement_utr"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "list_settlement_cycles",
        "description": "List every settlement cycle with its date, entry counts and net credit.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
