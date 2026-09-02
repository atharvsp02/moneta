from __future__ import annotations

import csv
import json
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path

from .schema import (
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


def _dt(value: str) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def load_settlements(path: Path) -> list[SettlementRow]:
    rows: list[SettlementRow] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            rows.append(
                SettlementRow(
                    entity_id=raw["entity_id"],
                    type=EntryType(raw["type"]),
                    debit=int(raw["debit"]),
                    credit=int(raw["credit"]),
                    amount=int(raw["amount"]),
                    fee=int(raw["fee"]),
                    tax=int(raw["tax"]),
                    settled=raw["settled"] == "true",
                    settled_at=_dt(raw["settled_at"]),
                    settlement_id=raw["settlement_id"] or None,
                    settlement_utr=raw["settlement_utr"] or None,
                    order_id=raw["order_id"],
                    method=Method(raw["method"]),
                    created_at=_dt(raw["created_at"]),
                )
            )
    return rows


def load_books(path: Path) -> list[Voucher]:
    grouped: OrderedDict[str, Voucher] = OrderedDict()
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            vid = raw["voucher_id"]
            if vid not in grouped:
                grouped[vid] = Voucher(
                    voucher_id=vid,
                    voucher_type=VoucherType(raw["voucher_type"]),
                    voucher_date=date.fromisoformat(raw["voucher_date"]),
                    narration=raw["narration"],
                    order_id=raw["order_id"] or None,
                    settlement_utr=raw["settlement_utr"] or None,
                    lines=[],
                )
            grouped[vid].lines.append(
                LedgerLine(Account(raw["account"]), int(raw["debit"]), int(raw["credit"]))
            )
    return list(grouped.values())


def derive_settlements(rows: list[SettlementRow]) -> list[Settlement]:
    seen: OrderedDict[str, Settlement] = OrderedDict()
    for row in rows:
        if not row.settlement_id:
            continue
        if row.settlement_id not in seen:
            seen[row.settlement_id] = Settlement(
                settlement_id=row.settlement_id,
                settlement_utr=row.settlement_utr,
                settled_at=row.settled_at,
                net_amount=0,
            )
        seen[row.settlement_id].net_amount += row.net
    return sorted(seen.values(), key=lambda s: s.settled_at)


def load_dataset(data_dir: Path, name: str) -> Dataset:
    data_dir = Path(data_dir)
    rows = load_settlements(data_dir / f"{name}.settlements.csv")
    vouchers = load_books(data_dir / f"{name}.books.csv")
    return Dataset(settlement_rows=rows, vouchers=vouchers, settlements=derive_settlements(rows))


def load_labels(data_dir: Path, name: str) -> list[dict]:
    return json.loads((Path(data_dir) / f"{name}.labels.json").read_text(encoding="utf-8"))
