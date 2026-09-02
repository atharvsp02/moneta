from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum


class EntryType(str, Enum):
    PAYMENT = "payment"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class Method(str, Enum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    WALLET = "wallet"


class Account(str, Enum):
    SALES = "Sales"
    GST_OUTPUT = "GST Output Tax"
    RAZORPAY_CLEARING = "Razorpay Clearing"
    BANK = "Bank"
    PG_CHARGES = "Payment Gateway Charges"
    GST_INPUT_CREDIT = "GST Input Credit"
    SALES_RETURN = "Sales Return"


class VoucherType(str, Enum):
    SALES = "sales"
    RECEIPT = "receipt"
    CREDIT_NOTE = "credit_note"


MDR_BPS = {
    Method.UPI: 0,
    Method.CARD: 200,
    Method.NETBANKING: 200,
    Method.WALLET: 200,
}


@dataclass
class SettlementRow:
    entity_id: str
    type: EntryType
    debit: int
    credit: int
    amount: int
    fee: int
    tax: int
    settled: bool
    settled_at: datetime | None
    settlement_id: str | None
    settlement_utr: str | None
    order_id: str
    method: Method
    created_at: datetime

    @property
    def net(self) -> int:
        return self.credit - self.debit

    def to_row(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["method"] = self.method.value
        d["settled_at"] = self.settled_at.isoformat() if self.settled_at else ""
        d["created_at"] = self.created_at.isoformat()
        d["settled"] = "true" if self.settled else "false"
        d["settlement_id"] = self.settlement_id or ""
        d["settlement_utr"] = self.settlement_utr or ""
        return d


@dataclass
class LedgerLine:
    account: Account
    debit: int
    credit: int


@dataclass
class Voucher:
    voucher_id: str
    voucher_type: VoucherType
    voucher_date: date
    narration: str
    lines: list[LedgerLine]
    order_id: str | None = None
    settlement_utr: str | None = None

    @property
    def total_debit(self) -> int:
        return sum(line.debit for line in self.lines)

    @property
    def total_credit(self) -> int:
        return sum(line.credit for line in self.lines)

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit

    def amount_on(self, account: Account) -> int:
        return sum(
            line.debit + line.credit for line in self.lines if line.account is account
        )

    def net_on(self, account: Account) -> int:
        return sum(
            line.debit - line.credit for line in self.lines if line.account is account
        )

    def to_rows(self) -> list[dict]:
        return [
            {
                "voucher_id": self.voucher_id,
                "voucher_type": self.voucher_type.value,
                "voucher_date": self.voucher_date.isoformat(),
                "narration": self.narration,
                "order_id": self.order_id or "",
                "settlement_utr": self.settlement_utr or "",
                "account": line.account.value,
                "debit": line.debit,
                "credit": line.credit,
            }
            for line in self.lines
        ]


@dataclass
class Settlement:
    settlement_id: str
    settlement_utr: str
    settled_at: datetime
    net_amount: int


@dataclass
class Dataset:
    settlement_rows: list[SettlementRow] = field(default_factory=list)
    vouchers: list[Voucher] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
