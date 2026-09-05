"""Builders for hand-made reconciliation scenarios.

The generator produces realistic datasets, but a test that asserts on 120 random
orders tells you little when it fails. These helpers build the smallest dataset that
exhibits one specific condition, so a failure names the condition.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from moneta.money import bps_of, gst_on
from moneta.schema import (
    MDR_BPS,
    Account,
    Dataset,
    EntryType,
    LedgerLine,
    Method,
    Settlement,
    SettlementRow,
    Voucher,
    VoucherType,
)

BASE = datetime(2026, 8, 1, 10, 0, 0)


def payment(
    order_id: str,
    amount: int,
    *,
    method: Method = Method.CARD,
    settlement_id: str | None = "setl_1",
    utr: str | None = "UTR0001",
    settled: bool = True,
    fee: int | None = None,
    tax: int | None = None,
) -> SettlementRow:
    fee = bps_of(amount, MDR_BPS[method]) if fee is None else fee
    tax = gst_on(fee) if tax is None else tax
    return SettlementRow(
        entity_id=f"pay_{order_id}",
        type=EntryType.PAYMENT,
        debit=0,
        credit=amount - fee - tax,
        amount=amount,
        fee=fee,
        tax=tax,
        settled=settled,
        settled_at=BASE if settled else None,
        settlement_id=settlement_id if settled else None,
        settlement_utr=utr if settled else None,
        order_id=order_id,
        method=method,
        created_at=BASE,
    )


def refund(
    order_id: str,
    amount: int,
    *,
    settlement_id: str | None = "setl_1",
    utr: str | None = "UTR0001",
) -> SettlementRow:
    return SettlementRow(
        entity_id=f"rfnd_{order_id}",
        type=EntryType.REFUND,
        debit=amount,
        credit=0,
        amount=amount,
        fee=0,
        tax=0,
        settled=True,
        settled_at=BASE,
        settlement_id=settlement_id,
        settlement_utr=utr,
        order_id=order_id,
        method=Method.CARD,
        created_at=BASE,
    )


def sales_voucher(order_id: str, gross: int, *, vid: str | None = None) -> Voucher:
    """A sales voucher whose Razorpay Clearing debit is the gross the engine matches on."""
    gst = round(gross * 18 / 118)
    return Voucher(
        voucher_id=vid or f"SV-{order_id}",
        voucher_type=VoucherType.SALES,
        voucher_date=date(2026, 8, 1),
        narration=f"Sale {order_id}",
        order_id=order_id,
        lines=[
            LedgerLine(Account.RAZORPAY_CLEARING, gross, 0),
            LedgerLine(Account.SALES, 0, gross - gst),
            LedgerLine(Account.GST_OUTPUT, 0, gst),
        ],
    )


def credit_note(order_id: str, amount: int) -> Voucher:
    gst = round(amount * 18 / 118)
    return Voucher(
        voucher_id=f"CN-{order_id}",
        voucher_type=VoucherType.CREDIT_NOTE,
        voucher_date=date(2026, 8, 2),
        narration=f"Refund {order_id}",
        order_id=order_id,
        lines=[
            LedgerLine(Account.SALES_RETURN, amount - gst, 0),
            LedgerLine(Account.GST_OUTPUT, gst, 0),
            LedgerLine(Account.RAZORPAY_CLEARING, 0, amount),
        ],
    )


def receipt(utr: str, bank: int, fee: int, tax: int, *, vid: str = "RV-1") -> Voucher:
    return Voucher(
        voucher_id=vid,
        voucher_type=VoucherType.RECEIPT,
        voucher_date=date(2026, 8, 3),
        narration=f"Settlement {utr}",
        settlement_utr=utr,
        lines=[
            LedgerLine(Account.BANK, bank, 0),
            LedgerLine(Account.PG_CHARGES, fee, 0),
            LedgerLine(Account.GST_INPUT_CREDIT, tax, 0),
            LedgerLine(Account.RAZORPAY_CLEARING, 0, bank + fee + tax),
        ],
    )


def settlement(rows: list[SettlementRow], utr: str = "UTR0001", sid: str = "setl_1") -> Settlement:
    """A settlement whose net equals the sum of its rows, i.e. internally consistent."""
    return Settlement(
        settlement_id=sid,
        settlement_utr=utr,
        settled_at=BASE,
        net_amount=sum(r.net for r in rows if r.settlement_id == sid),
    )


def dataset(rows: list[SettlementRow], vouchers: list[Voucher], settlements=None) -> Dataset:
    return Dataset(
        settlement_rows=rows,
        vouchers=vouchers,
        settlements=settlements if settlements is not None else [settlement(rows)],
    )


@pytest.fixture
def clean_pair():
    """One settled card payment, correctly booked. The baseline that must reconcile."""
    row = payment("order_1", 10_000)
    stl = settlement([row])
    rcpt = receipt("UTR0001", stl.net_amount, row.fee, row.tax)
    return dataset([row], [sales_voucher("order_1", 10_000), rcpt], [stl])
