from __future__ import annotations

import csv
import json
import random
import string
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from .money import bps_of, gst_on, round_half_up, to_paise
from .schema import (
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

ALPHABET = string.ascii_letters + string.digits
METHOD_WEIGHTS = [(Method.UPI, 42), (Method.CARD, 33), (Method.NETBANKING, 15), (Method.WALLET, 10)]
SALE_GST_PCT = 18
SETTLEMENT_LAG_DAYS = 2
SETTLEMENT_HOUR = 11


@dataclass
class Label:
    order_id: str
    error_type: str
    description: str
    impact_paise: int
    side: str


@dataclass
class Order:
    order_id: str
    payment_id: str
    created_at: datetime
    amount: int
    method: Method
    fee: int
    tax: int
    refund_id: str | None = None
    refund_amount: int = 0
    refund_at: datetime | None = None
    settlement_cycle: date | None = None
    refund_cycle: date | None = None
    settled: bool = False


def _rid(rng: random.Random, prefix: str) -> str:
    return prefix + "".join(rng.choice(ALPHABET) for _ in range(14))


def _utr(rng: random.Random) -> str:
    return "".join(rng.choice(string.digits) for _ in range(12))


def _pick_method(rng: random.Random) -> Method:
    total = sum(w for _, w in METHOD_WEIGHTS)
    draw = rng.uniform(0, total)
    upto = 0.0
    for method, weight in METHOD_WEIGHTS:
        upto += weight
        if draw <= upto:
            return method
    return METHOD_WEIGHTS[-1][0]


def _pick_amount(rng: random.Random) -> int:
    tier = rng.random()
    if tier < 0.45:
        rupee = rng.uniform(199, 1500)
    elif tier < 0.85:
        rupee = rng.uniform(1500, 6000)
    else:
        rupee = rng.uniform(6000, 24000)
    return to_paise(round(rupee, 2))


def _split_gst_inclusive(gross: int) -> tuple[int, int]:
    taxable = round_half_up(Decimal(gross) * Decimal(100) / Decimal(100 + SALE_GST_PCT))
    return taxable, gross - taxable


def _sales_voucher(order: Order, seq: int) -> Voucher:
    taxable, gst = _split_gst_inclusive(order.amount)
    return Voucher(
        voucher_id=f"SV-{seq:04d}",
        voucher_type=VoucherType.SALES,
        voucher_date=order.created_at.date(),
        narration=f"Online sale via Razorpay {order.method.value.upper()} / {order.order_id}",
        order_id=order.order_id,
        lines=[
            LedgerLine(Account.RAZORPAY_CLEARING, order.amount, 0),
            LedgerLine(Account.SALES, 0, taxable),
            LedgerLine(Account.GST_OUTPUT, 0, gst),
        ],
    )


def _credit_note(order: Order, seq: int) -> Voucher:
    taxable, gst = _split_gst_inclusive(order.refund_amount)
    return Voucher(
        voucher_id=f"CN-{seq:04d}",
        voucher_type=VoucherType.CREDIT_NOTE,
        voucher_date=order.refund_at.date(),
        narration=f"Refund against {order.order_id}",
        order_id=order.order_id,
        lines=[
            LedgerLine(Account.SALES_RETURN, taxable, 0),
            LedgerLine(Account.GST_OUTPUT, gst, 0),
            LedgerLine(Account.RAZORPAY_CLEARING, 0, order.refund_amount),
        ],
    )


def _receipt_voucher(
    settlement: Settlement, gross: int, fee: int, tax: int, refunds: int, seq: int
) -> Voucher:
    return Voucher(
        voucher_id=f"RV-{seq:04d}",
        voucher_type=VoucherType.RECEIPT,
        voucher_date=settlement.settled_at.date(),
        narration=f"Razorpay settlement {settlement.settlement_utr}",
        settlement_utr=settlement.settlement_utr,
        lines=[
            LedgerLine(Account.BANK, gross - fee - tax - refunds, 0),
            LedgerLine(Account.PG_CHARGES, fee, 0),
            LedgerLine(Account.GST_INPUT_CREDIT, tax, 0),
            LedgerLine(Account.RAZORPAY_CLEARING, 0, gross - refunds),
        ],
    )


def _build_orders(rng: random.Random, n_orders: int, start: date, days: int) -> list[Order]:
    orders: list[Order] = []
    for _ in range(n_orders):
        day_offset = rng.randrange(days)
        created = datetime.combine(start + timedelta(days=day_offset), time(0, 0)) + timedelta(
            seconds=rng.randrange(9 * 3600, 22 * 3600)
        )
        method = _pick_method(rng)
        amount = _pick_amount(rng)
        fee = bps_of(amount, MDR_BPS[method])
        tax = gst_on(fee)
        order = Order(
            order_id=_rid(rng, "order_"),
            payment_id=_rid(rng, "pay_"),
            created_at=created,
            amount=amount,
            method=method,
            fee=fee,
            tax=tax,
        )
        if rng.random() < 0.09:
            order.refund_id = _rid(rng, "rfnd_")
            partial = rng.random() < 0.4
            order.refund_amount = (
                round_half_up(Decimal(amount) * Decimal(str(round(rng.uniform(0.2, 0.6), 4))))
                if partial
                else amount
            )
            order.refund_at = created + timedelta(days=rng.randrange(1, 6), hours=rng.randrange(24))
        orders.append(order)
    orders.sort(key=lambda o: o.created_at)
    return orders


def _assign_cycles(orders: list[Order], start: date, days: int) -> date:
    cutoff = start + timedelta(days=days - 1)
    for order in orders:
        order.settlement_cycle = order.created_at.date() + timedelta(days=SETTLEMENT_LAG_DAYS)
        order.settled = order.settlement_cycle <= cutoff
        if order.refund_at:
            order.refund_cycle = order.refund_at.date() + timedelta(days=SETTLEMENT_LAG_DAYS)
    return cutoff


def build_dataset(
    seed: int = 7,
    n_orders: int = 120,
    start: date = date(2026, 8, 3),
    days: int = 21,
    inject: bool = True,
) -> tuple[Dataset, list[Label], list[Order]]:
    rng = random.Random(seed)
    orders = _build_orders(rng, n_orders, start, days)
    cutoff = _assign_cycles(orders, start, days)

    cycles = sorted({o.settlement_cycle for o in orders if o.settled})
    settlements: dict[date, Settlement] = {}
    for cycle in cycles:
        settlements[cycle] = Settlement(
            settlement_id=_rid(rng, "setl_"),
            settlement_utr=_utr(rng),
            settled_at=datetime.combine(cycle, time(SETTLEMENT_HOUR, 0)),
            net_amount=0,
        )

    rows: list[SettlementRow] = []
    for order in orders:
        stl = settlements.get(order.settlement_cycle) if order.settled else None
        rows.append(
            SettlementRow(
                entity_id=order.payment_id,
                type=EntryType.PAYMENT,
                debit=0,
                credit=order.amount - order.fee - order.tax if stl else 0,
                amount=order.amount,
                fee=order.fee,
                tax=order.tax,
                settled=bool(stl),
                settled_at=stl.settled_at if stl else None,
                settlement_id=stl.settlement_id if stl else None,
                settlement_utr=stl.settlement_utr if stl else None,
                order_id=order.order_id,
                method=order.method,
                created_at=order.created_at,
            )
        )
        if order.refund_id and order.refund_cycle and order.refund_cycle <= cutoff:
            rstl = settlements.get(order.refund_cycle)
            if rstl:
                rows.append(
                    SettlementRow(
                        entity_id=order.refund_id,
                        type=EntryType.REFUND,
                        debit=order.refund_amount,
                        credit=0,
                        amount=order.refund_amount,
                        fee=0,
                        tax=0,
                        settled=True,
                        settled_at=rstl.settled_at,
                        settlement_id=rstl.settlement_id,
                        settlement_utr=rstl.settlement_utr,
                        order_id=order.order_id,
                        method=order.method,
                        created_at=order.refund_at,
                    )
                )

    for cycle, stl in settlements.items():
        stl.net_amount = sum(r.net for r in rows if r.settlement_id == stl.settlement_id)

    vouchers: list[Voucher] = []
    for i, order in enumerate(orders, start=1):
        vouchers.append(_sales_voucher(order, i))
    cn_seq = 1
    for order in orders:
        if order.refund_id and order.refund_at:
            vouchers.append(_credit_note(order, cn_seq))
            cn_seq += 1
    for i, cycle in enumerate(cycles, start=1):
        stl = settlements[cycle]
        cycle_rows = [r for r in rows if r.settlement_id == stl.settlement_id]
        gross = sum(r.amount for r in cycle_rows if r.type is EntryType.PAYMENT)
        fee = sum(r.fee for r in cycle_rows)
        tax = sum(r.tax for r in cycle_rows)
        refunds = sum(r.amount for r in cycle_rows if r.type is EntryType.REFUND)
        vouchers.append(_receipt_voucher(stl, gross, fee, tax, refunds, i))

    dataset = Dataset(
        settlement_rows=rows, vouchers=vouchers, settlements=list(settlements.values())
    )
    labels = _inject_errors(rng, dataset, orders) if inject else []
    dataset.vouchers.sort(key=lambda v: (v.voucher_date, v.voucher_id))
    return dataset, labels, orders


ASSUMED_FLAT_BPS = 175

DEFAULT_ERROR_MIX = {
    "AGGREGATE_FEE_MISMATCH": 2,
    "GST_INPUT_ROUNDING_DRIFT": 1,
    "MISSING_REFUND_IN_BOOKS": 2,
    "DUPLICATE_BOOKING": 2,
    "MISSING_IN_BOOKS": 2,
    "AMOUNT_MISMATCH": 3,
    "CROSS_CYCLE_REFUND": 1,
}


def _sales_voucher_for(dataset: Dataset, order_id: str) -> Voucher | None:
    for v in dataset.vouchers:
        if v.voucher_type is VoucherType.SALES and v.order_id == order_id:
            return v
    return None


def _receipt_for(dataset: Dataset, utr: str) -> Voucher | None:
    for v in dataset.vouchers:
        if v.voucher_type is VoucherType.RECEIPT and v.settlement_utr == utr:
            return v
    return None


def _rebalance_receipt(voucher: Voucher, bank: int, fee: int, tax: int) -> None:
    voucher.lines = [
        LedgerLine(Account.BANK, bank, 0),
        LedgerLine(Account.PG_CHARGES, fee, 0),
        LedgerLine(Account.GST_INPUT_CREDIT, tax, 0),
        LedgerLine(Account.RAZORPAY_CLEARING, 0, bank + fee + tax),
    ]


def _transpose_digits(rng: random.Random, amount: int) -> int:
    digits = list(str(amount))
    if len(digits) < 3:
        return amount + 1000
    i = rng.randrange(len(digits) - 2)
    digits[i], digits[i + 1] = digits[i + 1], digits[i]
    swapped = int("".join(digits))
    return swapped if swapped != amount else amount + 1000


def _inject_errors(rng: random.Random, dataset: Dataset, orders: list[Order]) -> list[Label]:
    labels: list[Label] = []
    settled = [o for o in orders if o.settled]
    refunded = [o for o in settled if o.refund_id and o.refund_cycle]
    used: set[str] = set()

    def take(pool: list[Order]) -> Order | None:
        candidates = [o for o in pool if o.order_id not in used]
        if not candidates:
            return None
        chosen = rng.choice(candidates)
        used.add(chosen.order_id)
        return chosen

    receipts = [v for v in dataset.vouchers if v.voucher_type is VoucherType.RECEIPT]
    rng.shuffle(receipts)
    receipt_pool = list(receipts)

    settled_refund_ids = {
        r.order_id
        for r in dataset.settlement_rows
        if r.type is EntryType.REFUND and r.settlement_id
    }
    latest_settlement = max((s.settled_at for s in dataset.settlements), default=None)
    reserved_cross_cycle: list[Order] = []
    for _ in range(DEFAULT_ERROR_MIX["CROSS_CYCLE_REFUND"]):
        pool = [
            o
            for o in refunded
            if o.order_id not in used
            and o.order_id in settled_refund_ids
            and any(
                s.settled_at > r.settled_at
                for r in dataset.settlement_rows
                if r.order_id == o.order_id and r.type is EntryType.REFUND and r.settled_at
                for s in dataset.settlements
            )
        ]
        partials = [o for o in pool if 0 < o.refund_amount < o.amount]
        candidates = partials or pool
        if not candidates:
            break
        chosen = rng.choice(candidates)
        used.add(chosen.order_id)
        reserved_cross_cycle.append(chosen)

    for _ in range(DEFAULT_ERROR_MIX["AGGREGATE_FEE_MISMATCH"]):
        if not receipt_pool:
            break
        voucher = receipt_pool.pop()
        rows = [r for r in dataset.settlement_rows if r.settlement_utr == voucher.settlement_utr]
        gross = sum(r.amount for r in rows if r.type is EntryType.PAYMENT)
        actual_fee = sum(r.fee for r in rows)
        actual_tax = sum(r.tax for r in rows)
        bank = voucher.net_on(Account.BANK)
        assumed_fee = bps_of(gross, ASSUMED_FLAT_BPS)
        assumed_tax = gst_on(assumed_fee)
        _rebalance_receipt(voucher, bank, assumed_fee, assumed_tax)
        labels.append(
            Label(
                order_id=voucher.settlement_utr,
                error_type="AGGREGATE_FEE_MISMATCH",
                description=(
                    f"Books booked gateway fee at an assumed flat {ASSUMED_FLAT_BPS/100:.2f}% "
                    f"on gross; Razorpay charged per-method MDR (UPI is zero-rated)."
                ),
                impact_paise=(assumed_fee + assumed_tax) - (actual_fee + actual_tax),
                side="books",
            )
        )

    for _ in range(DEFAULT_ERROR_MIX["GST_INPUT_ROUNDING_DRIFT"]):
        if not receipt_pool:
            break
        voucher = receipt_pool.pop()
        rows = [r for r in dataset.settlement_rows if r.settlement_utr == voucher.settlement_utr]
        actual_fee = sum(r.fee for r in rows)
        actual_tax = sum(r.tax for r in rows)
        aggregate_tax = gst_on(actual_fee)
        if aggregate_tax == actual_tax:
            aggregate_tax = actual_tax - rng.randrange(2, 7)
        bank = voucher.net_on(Account.BANK)
        _rebalance_receipt(voucher, bank, actual_fee, aggregate_tax)
        labels.append(
            Label(
                order_id=voucher.settlement_utr,
                error_type="GST_INPUT_ROUNDING_DRIFT",
                description=(
                    "Books computed GST input credit as 18% of the aggregate fee; Razorpay "
                    "rounds GST per transaction, so the totals differ by a few paise."
                ),
                impact_paise=aggregate_tax - actual_tax,
                side="books",
            )
        )

    for _ in range(DEFAULT_ERROR_MIX["MISSING_REFUND_IN_BOOKS"]):
        order = take(refunded)
        if not order:
            break
        dataset.vouchers = [
            v
            for v in dataset.vouchers
            if not (v.voucher_type is VoucherType.CREDIT_NOTE and v.order_id == order.order_id)
        ]
        labels.append(
            Label(
                order_id=order.order_id,
                error_type="MISSING_REFUND_IN_BOOKS",
                description="Razorpay netted a refund out of settlement; no credit note exists in the books.",
                impact_paise=order.refund_amount,
                side="books",
            )
        )

    for _ in range(DEFAULT_ERROR_MIX["DUPLICATE_BOOKING"]):
        order = take(settled)
        if not order:
            break
        original = _sales_voucher_for(dataset, order.order_id)
        if not original:
            continue
        clone = Voucher(
            voucher_id=original.voucher_id + "-D",
            voucher_type=VoucherType.SALES,
            voucher_date=original.voucher_date,
            narration=original.narration,
            order_id=original.order_id,
            lines=[LedgerLine(l.account, l.debit, l.credit) for l in original.lines],
        )
        dataset.vouchers.append(clone)
        labels.append(
            Label(
                order_id=order.order_id,
                error_type="DUPLICATE_BOOKING",
                description="The same order was entered twice in the sales ledger.",
                impact_paise=order.amount,
                side="books",
            )
        )

    for _ in range(DEFAULT_ERROR_MIX["MISSING_IN_BOOKS"]):
        order = take(settled)
        if not order:
            break
        dataset.vouchers = [
            v
            for v in dataset.vouchers
            if not (v.voucher_type is VoucherType.SALES and v.order_id == order.order_id)
        ]
        labels.append(
            Label(
                order_id=order.order_id,
                error_type="MISSING_IN_BOOKS",
                description="Razorpay settled this payment but the sales voucher was never recorded.",
                impact_paise=order.amount,
                side="books",
            )
        )

    for _ in range(DEFAULT_ERROR_MIX["AMOUNT_MISMATCH"]):
        order = take(settled)
        if not order:
            break
        voucher = _sales_voucher_for(dataset, order.order_id)
        if not voucher:
            continue
        wrong = _transpose_digits(rng, order.amount)
        taxable, gst = _split_gst_inclusive(wrong)
        voucher.lines = [
            LedgerLine(Account.RAZORPAY_CLEARING, wrong, 0),
            LedgerLine(Account.SALES, 0, taxable),
            LedgerLine(Account.GST_OUTPUT, 0, gst),
        ]
        labels.append(
            Label(
                order_id=order.order_id,
                error_type="AMOUNT_MISMATCH",
                description="Sales voucher gross does not equal the payment amount Razorpay settled (digit transposition).",
                impact_paise=wrong - order.amount,
                side="books",
            )
        )

    for order in reserved_cross_cycle:
        refund_rows = [
            r
            for r in dataset.settlement_rows
            if r.order_id == order.order_id and r.type is EntryType.REFUND
        ]
        if not refund_rows:
            continue
        row = refund_rows[0]
        later = sorted(
            (s for s in dataset.settlements if s.settled_at > row.settled_at),
            key=lambda s: s.settled_at,
        )
        if not later:
            continue
        target = later[0]
        row.settlement_id = target.settlement_id
        row.settlement_utr = target.settlement_utr
        row.settled_at = target.settled_at
        labels.append(
            Label(
                order_id=order.order_id,
                error_type="CROSS_CYCLE_REFUND",
                description=(
                    "Books recorded the credit note in the cycle the refund was issued, but "
                    "Razorpay netted it out of the following settlement cycle."
                ),
                impact_paise=order.refund_amount,
                side="timing",
            )
        )

    for stl in dataset.settlements:
        stl.net_amount = sum(
            r.net for r in dataset.settlement_rows if r.settlement_id == stl.settlement_id
        )
    return labels


SETTLEMENT_FIELDS = [
    "entity_id",
    "type",
    "debit",
    "credit",
    "amount",
    "fee",
    "tax",
    "settled",
    "settled_at",
    "settlement_id",
    "settlement_utr",
    "order_id",
    "method",
    "created_at",
]

BOOKS_FIELDS = [
    "voucher_id",
    "voucher_type",
    "voucher_date",
    "narration",
    "order_id",
    "settlement_utr",
    "account",
    "debit",
    "credit",
]


def write_dataset(dataset: Dataset, labels: list[Label], out_dir: Path, name: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    settlements_path = out_dir / f"{name}.settlements.csv"
    books_path = out_dir / f"{name}.books.csv"
    labels_path = out_dir / f"{name}.labels.json"

    with settlements_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SETTLEMENT_FIELDS)
        writer.writeheader()
        for row in dataset.settlement_rows:
            writer.writerow(row.to_row())

    with books_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=BOOKS_FIELDS)
        writer.writeheader()
        for voucher in dataset.vouchers:
            for line in voucher.to_rows():
                writer.writerow(line)

    labels_path.write_text(
        json.dumps([asdict(l) for l in labels], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "settlements": str(settlements_path),
        "books": str(books_path),
        "labels": str(labels_path),
        "settlement_rows": len(dataset.settlement_rows),
        "voucher_count": len(dataset.vouchers),
        "ledger_lines": sum(len(v.lines) for v in dataset.vouchers),
        "settlements_count": len(dataset.settlements),
        "injected_errors": len(labels),
    }


def generate(seed: int, n_orders: int, out_dir: Path, name: str) -> dict:
    dataset, labels, _ = build_dataset(seed=seed, n_orders=n_orders)
    return write_dataset(dataset, labels, out_dir, name)
