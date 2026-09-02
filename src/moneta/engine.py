from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from .money import rupees
from .schema import Account, Dataset, EntryType, SettlementRow, Voucher, VoucherType


class ExceptionClass(str, Enum):
    MISSING_IN_BOOKS = "MISSING_IN_BOOKS"
    MISSING_IN_SETTLEMENT = "MISSING_IN_SETTLEMENT"
    DUPLICATE_BOOKING = "DUPLICATE_BOOKING"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    MISSING_REFUND_IN_BOOKS = "MISSING_REFUND_IN_BOOKS"
    REFUND_AMOUNT_MISMATCH = "REFUND_AMOUNT_MISMATCH"
    AGGREGATE_FEE_MISMATCH = "AGGREGATE_FEE_MISMATCH"
    GST_INPUT_ROUNDING_DRIFT = "GST_INPUT_ROUNDING_DRIFT"
    CROSS_CYCLE_REFUND = "CROSS_CYCLE_REFUND"
    SETTLEMENT_NET_MISMATCH = "SETTLEMENT_NET_MISMATCH"
    UNKNOWN = "UNKNOWN"


@dataclass
class Discrepancy:
    exception_id: str
    scope: str
    key: str
    delta_paise: int
    rule: str
    detected_by: str
    classification: str | None
    confidence: float | None
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "exception_id": self.exception_id,
            "scope": self.scope,
            "key": self.key,
            "delta_paise": self.delta_paise,
            "delta": rupees(self.delta_paise),
            "rule": self.rule,
            "detected_by": self.detected_by,
            "classification": self.classification,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class OrderMatch:
    order_id: str
    entity_id: str
    entry_type: str
    settlement_amount: int
    books_amount: int | None
    matched: bool
    settled: bool
    reason: str


@dataclass
class Reconciliation:
    order_matches: list[OrderMatch] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    settlement_checks: list[dict] = field(default_factory=list)
    clearing: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    runtime_ms: float = 0.0

    @property
    def open_investigations(self) -> list[Discrepancy]:
        return [d for d in self.discrepancies if d.classification is None]


def _index_books(vouchers: list[Voucher]):
    sales: dict[str, list[Voucher]] = defaultdict(list)
    credit_notes: dict[str, list[Voucher]] = defaultdict(list)
    receipts: dict[str, Voucher] = {}
    for v in vouchers:
        if v.voucher_type is VoucherType.SALES and v.order_id:
            sales[v.order_id].append(v)
        elif v.voucher_type is VoucherType.CREDIT_NOTE and v.order_id:
            credit_notes[v.order_id].append(v)
        elif v.voucher_type is VoucherType.RECEIPT and v.settlement_utr:
            receipts[v.settlement_utr] = v
    return sales, credit_notes, receipts


def _ev(source: str, detail: str, **data) -> dict:
    return {"source": source, "detail": detail, "data": data}


def reconcile(dataset: Dataset) -> Reconciliation:
    started = time.perf_counter()
    result = Reconciliation()
    sales, credit_notes, receipts = _index_books(dataset.vouchers)

    payments = [r for r in dataset.settlement_rows if r.type is EntryType.PAYMENT]
    refunds = [r for r in dataset.settlement_rows if r.type is EntryType.REFUND]
    payment_order_ids = {r.order_id for r in payments}
    refund_order_ids = {r.order_id for r in refunds}
    seq = 0

    def next_id(prefix: str) -> str:
        nonlocal seq
        seq += 1
        return f"{prefix}-{seq:04d}"

    for row in payments:
        booked = sales.get(row.order_id, [])
        if not booked:
            result.order_matches.append(
                OrderMatch(row.order_id, row.entity_id, "payment", row.amount, None, False,
                           row.settled, "no sales voucher in books")
            )
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="order",
                    key=row.order_id,
                    delta_paise=row.amount,
                    rule="payment_present_in_settlement_without_sales_voucher",
                    detected_by="rules",
                    classification=ExceptionClass.MISSING_IN_BOOKS.value,
                    confidence=1.0,
                    evidence=[
                        _ev("settlement", "Razorpay settled this payment",
                            entity_id=row.entity_id, amount=rupees(row.amount),
                            settled_at=row.settled_at.isoformat() if row.settled_at else None,
                            settlement_utr=row.settlement_utr),
                        _ev("books", "No sales voucher carries this order_id", order_id=row.order_id),
                    ],
                )
            )
            continue

        if len(booked) > 1:
            extra = sum(v.net_on(Account.RAZORPAY_CLEARING) for v in booked[1:])
            result.order_matches.append(
                OrderMatch(row.order_id, row.entity_id, "payment", row.amount,
                           sum(v.net_on(Account.RAZORPAY_CLEARING) for v in booked), False,
                           row.settled, f"{len(booked)} sales vouchers for one payment")
            )
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="order",
                    key=row.order_id,
                    delta_paise=extra,
                    rule="multiple_sales_vouchers_for_single_payment",
                    detected_by="rules",
                    classification=ExceptionClass.DUPLICATE_BOOKING.value,
                    confidence=1.0,
                    evidence=[
                        _ev("books", "Duplicate sales vouchers",
                            voucher_ids=[v.voucher_id for v in booked]),
                        _ev("settlement", "Razorpay recorded exactly one payment",
                            entity_id=row.entity_id, amount=rupees(row.amount)),
                    ],
                )
            )
            continue

        voucher = booked[0]
        books_gross = voucher.net_on(Account.RAZORPAY_CLEARING)
        if books_gross == row.amount:
            result.order_matches.append(
                OrderMatch(row.order_id, row.entity_id, "payment", row.amount, books_gross,
                           True, row.settled, "exact match on order_id and gross amount")
            )
        else:
            result.order_matches.append(
                OrderMatch(row.order_id, row.entity_id, "payment", row.amount, books_gross,
                           False, row.settled, "gross amount differs")
            )
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="order",
                    key=row.order_id,
                    delta_paise=books_gross - row.amount,
                    rule="sales_voucher_gross_not_equal_to_payment_amount",
                    detected_by="rules",
                    classification=ExceptionClass.AMOUNT_MISMATCH.value,
                    confidence=1.0,
                    evidence=[
                        _ev("settlement", "Payment amount", amount=rupees(row.amount),
                            entity_id=row.entity_id),
                        _ev("books", "Sales voucher gross", voucher_id=voucher.voucher_id,
                            amount=rupees(books_gross)),
                    ],
                )
            )

    for order_id, booked in sales.items():
        if order_id in payment_order_ids:
            continue
        gross = sum(v.net_on(Account.RAZORPAY_CLEARING) for v in booked)
        result.discrepancies.append(
            Discrepancy(
                exception_id=next_id("EX"),
                scope="order",
                key=order_id,
                delta_paise=-gross,
                rule="sales_voucher_without_matching_settlement_row",
                detected_by="rules",
                classification=ExceptionClass.MISSING_IN_SETTLEMENT.value,
                confidence=1.0,
                evidence=[
                    _ev("books", "Sales voucher exists",
                        voucher_ids=[v.voucher_id for v in booked], amount=rupees(gross)),
                    _ev("settlement", "No payment row carries this order_id", order_id=order_id),
                ],
            )
        )

    for row in refunds:
        notes = credit_notes.get(row.order_id, [])
        booked_amount = sum(v.net_on(Account.SALES_RETURN) + v.net_on(Account.GST_OUTPUT) for v in notes)
        if not notes:
            result.order_matches.append(
                OrderMatch(row.order_id, row.entity_id, "refund", row.amount, None, False,
                           row.settled, "no credit note in books")
            )
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="order",
                    key=row.order_id,
                    delta_paise=row.amount,
                    rule="refund_netted_by_razorpay_without_credit_note",
                    detected_by="rules",
                    classification=ExceptionClass.MISSING_REFUND_IN_BOOKS.value,
                    confidence=1.0,
                    evidence=[
                        _ev("settlement", "Refund netted out of settlement",
                            entity_id=row.entity_id, amount=rupees(row.amount),
                            settlement_utr=row.settlement_utr),
                        _ev("books", "No credit note carries this order_id", order_id=row.order_id),
                    ],
                )
            )
        elif booked_amount != row.amount:
            result.order_matches.append(
                OrderMatch(row.order_id, row.entity_id, "refund", row.amount, booked_amount,
                           False, row.settled, "credit note amount differs")
            )
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="order",
                    key=row.order_id,
                    delta_paise=booked_amount - row.amount,
                    rule="credit_note_amount_not_equal_to_refund_amount",
                    detected_by="rules",
                    classification=ExceptionClass.REFUND_AMOUNT_MISMATCH.value,
                    confidence=1.0,
                    evidence=[
                        _ev("settlement", "Refund amount", amount=rupees(row.amount)),
                        _ev("books", "Credit note total",
                            voucher_ids=[v.voucher_id for v in notes], amount=rupees(booked_amount)),
                    ],
                )
            )
        else:
            result.order_matches.append(
                OrderMatch(row.order_id, row.entity_id, "refund", row.amount, booked_amount,
                           True, row.settled, "exact match on order_id and refund amount")
            )

    unsettled_refund_paise = 0
    for order_id, notes in credit_notes.items():
        if order_id in refund_order_ids:
            continue
        unsettled_refund_paise += sum(v.amount_on(Account.RAZORPAY_CLEARING) for v in notes)

    for stl in dataset.settlements:
        rows = [r for r in dataset.settlement_rows if r.settlement_id == stl.settlement_id]
        gross = sum(r.amount for r in rows if r.type is EntryType.PAYMENT)
        fee = sum(r.fee for r in rows)
        tax = sum(r.tax for r in rows)
        refunded = sum(r.amount for r in rows if r.type is EntryType.REFUND)
        reconstructed_net = gross - fee - tax - refunded
        receipt = receipts.get(stl.settlement_utr)
        check = {
            "settlement_id": stl.settlement_id,
            "settlement_utr": stl.settlement_utr,
            "settled_at": stl.settled_at.isoformat(),
            "payment_count": sum(1 for r in rows if r.type is EntryType.PAYMENT),
            "refund_count": sum(1 for r in rows if r.type is EntryType.REFUND),
            "gross_paise": gross,
            "fee_paise": fee,
            "tax_paise": tax,
            "refund_paise": refunded,
            "reconstructed_net_paise": reconstructed_net,
            "reported_net_paise": stl.net_amount,
            "booked": receipt is not None,
        }
        if reconstructed_net != stl.net_amount:
            check["internal_inconsistency_paise"] = stl.net_amount - reconstructed_net
        if receipt is None:
            check["booked_net_paise"] = None
            result.settlement_checks.append(check)
            continue

        booked_bank = receipt.net_on(Account.BANK)
        booked_fee = receipt.net_on(Account.PG_CHARGES)
        booked_tax = receipt.net_on(Account.GST_INPUT_CREDIT)
        check.update(
            booked_net_paise=booked_bank,
            booked_fee_paise=booked_fee,
            booked_tax_paise=booked_tax,
            bank_delta_paise=booked_bank - reconstructed_net,
            fee_delta_paise=booked_fee - fee,
            tax_delta_paise=booked_tax - tax,
        )
        result.settlement_checks.append(check)

        if booked_fee != fee:
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="settlement",
                    key=stl.settlement_utr,
                    delta_paise=booked_fee - fee,
                    rule="booked_gateway_fee_not_equal_to_sum_of_per_order_fees",
                    detected_by="rules",
                    classification=None,
                    confidence=None,
                    evidence=[
                        _ev("books", "Gateway fee booked in receipt voucher",
                            voucher_id=receipt.voucher_id, amount=rupees(booked_fee)),
                        _ev("settlement", "Sum of per-order fees Razorpay charged",
                            amount=rupees(fee), payment_count=check["payment_count"]),
                    ],
                )
            )
        if booked_tax != tax:
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="settlement",
                    key=stl.settlement_utr,
                    delta_paise=booked_tax - tax,
                    rule="booked_gst_input_credit_not_equal_to_sum_of_per_order_tax",
                    detected_by="rules",
                    classification=None,
                    confidence=None,
                    evidence=[
                        _ev("books", "GST input credit booked",
                            voucher_id=receipt.voucher_id, amount=rupees(booked_tax)),
                        _ev("settlement", "Sum of per-order GST on fee", amount=rupees(tax)),
                    ],
                )
            )
        if booked_bank != reconstructed_net:
            result.discrepancies.append(
                Discrepancy(
                    exception_id=next_id("EX"),
                    scope="settlement",
                    key=stl.settlement_utr,
                    delta_paise=booked_bank - reconstructed_net,
                    rule="booked_bank_credit_not_equal_to_reconstructed_settlement_net",
                    detected_by="rules",
                    classification=None,
                    confidence=None,
                    evidence=[
                        _ev("books", "Bank debit booked against this UTR",
                            voucher_id=receipt.voucher_id, amount=rupees(booked_bank)),
                        _ev("settlement",
                            "Reconstructed net = gross - fee - GST - refunds",
                            gross=rupees(gross), fee=rupees(fee), tax=rupees(tax),
                            refunds=rupees(refunded), net=rupees(reconstructed_net)),
                    ],
                )
            )

    unsettled_payment_paise = sum(r.amount for r in payments if not r.settled)
    actual_clearing = sum(v.net_on(Account.RAZORPAY_CLEARING) for v in dataset.vouchers)
    expected_clearing = unsettled_payment_paise - unsettled_refund_paise
    result.clearing = {
        "actual_paise": actual_clearing,
        "expected_timing_paise": expected_clearing,
        "unsettled_payments_paise": unsettled_payment_paise,
        "unsettled_refunds_paise": unsettled_refund_paise,
        "unexplained_paise": actual_clearing - expected_clearing,
    }

    matched = [m for m in result.order_matches if m.matched]
    total_records = len(result.order_matches) + sum(
        1 for d in result.discrepancies if d.classification == ExceptionClass.MISSING_IN_SETTLEMENT.value
    )
    matched_value = sum(m.settlement_amount for m in matched)
    total_value = sum(m.settlement_amount for m in result.order_matches)
    result.totals = {
        "records_total": total_records,
        "records_matched": len(matched),
        "records_exception": total_records - len(matched),
        "match_rate_records": round(len(matched) / total_records, 4) if total_records else 0.0,
        "value_total_paise": total_value,
        "value_matched_paise": matched_value,
        "match_rate_value": round(matched_value / total_value, 4) if total_value else 0.0,
        "unsettled_records": sum(1 for m in result.order_matches if not m.settled),
        "unsettled_value_paise": unsettled_payment_paise,
        "discrepancies_total": len(result.discrepancies),
        "resolved_by_rules": sum(1 for d in result.discrepancies if d.classification),
        "open_for_agent": len(result.open_investigations),
    }
    result.runtime_ms = (time.perf_counter() - started) * 1000
    return result
