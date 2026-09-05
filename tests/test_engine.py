"""The deterministic pass.

Two properties matter more than any individual rule. First, a correctly booked
settlement must produce zero exceptions — a reconciler that cries wolf is useless.
Second, the engine must leave a case *open* (classification None) exactly when several
causes would produce the same symptom, because that seam is what the agent is handed.
"""

from __future__ import annotations

from moneta.engine import ExceptionClass, reconcile
from moneta.schema import Account

from conftest import (
    credit_note,
    dataset,
    payment,
    receipt,
    refund,
    sales_voucher,
    settlement,
)


def classifications(recon):
    return [d.classification for d in recon.discrepancies]


class TestCleanReconciliation:
    def test_correct_books_produce_no_exceptions(self, clean_pair):
        recon = reconcile(clean_pair)
        assert recon.discrepancies == []
        assert recon.totals["records_matched"] == 1
        assert recon.totals["match_rate_records"] == 1.0
        assert recon.totals["match_rate_value"] == 1.0

    def test_upi_zero_fee_reconciles(self):
        from moneta.schema import Method

        row = payment("order_upi", 5_000, method=Method.UPI)
        assert row.fee == 0 and row.tax == 0
        stl = settlement([row])
        ds = dataset(
            [row], [sales_voucher("order_upi", 5_000), receipt("UTR0001", stl.net_amount, 0, 0)], [stl]
        )
        assert reconcile(ds).discrepancies == []


class TestOrderLevelFaults:
    def test_settled_payment_with_no_sales_voucher(self):
        row = payment("order_1", 10_000)
        stl = settlement([row])
        ds = dataset([row], [receipt("UTR0001", stl.net_amount, row.fee, row.tax)], [stl])
        recon = reconcile(ds)
        assert ExceptionClass.MISSING_IN_BOOKS.value in classifications(recon)
        assert not recon.order_matches[0].matched

    def test_sales_voucher_with_no_settlement_row(self):
        ds = dataset([], [sales_voucher("ghost", 4_200)], [])
        recon = reconcile(ds)
        assert ExceptionClass.MISSING_IN_SETTLEMENT.value in classifications(recon)

    def test_two_vouchers_for_one_payment(self):
        row = payment("order_1", 10_000)
        stl = settlement([row])
        ds = dataset(
            [row],
            [
                sales_voucher("order_1", 10_000, vid="SV-a"),
                sales_voucher("order_1", 10_000, vid="SV-b"),
                receipt("UTR0001", stl.net_amount, row.fee, row.tax),
            ],
            [stl],
        )
        recon = reconcile(ds)
        assert ExceptionClass.DUPLICATE_BOOKING.value in classifications(recon)

    def test_gross_amount_disagreement(self):
        row = payment("order_1", 10_000)
        stl = settlement([row])
        ds = dataset(
            [row],
            [sales_voucher("order_1", 19_000), receipt("UTR0001", stl.net_amount, row.fee, row.tax)],
            [stl],
        )
        recon = reconcile(ds)
        mismatch = next(
            d for d in recon.discrepancies if d.classification == ExceptionClass.AMOUNT_MISMATCH.value
        )
        # Delta is signed books-minus-Razorpay, so a controller can see the direction.
        assert mismatch.delta_paise == 9_000

    def test_refund_netted_without_credit_note(self):
        pay = payment("order_1", 10_000)
        ref = refund("order_1", 3_000)
        stl = settlement([pay, ref])
        ds = dataset(
            [pay, ref],
            [sales_voucher("order_1", 10_000), receipt("UTR0001", stl.net_amount, pay.fee, pay.tax)],
            [stl],
        )
        recon = reconcile(ds)
        assert ExceptionClass.MISSING_REFUND_IN_BOOKS.value in classifications(recon)

    def test_matched_refund_raises_nothing(self):
        pay = payment("order_1", 10_000)
        ref = refund("order_1", 3_000)
        stl = settlement([pay, ref])
        ds = dataset(
            [pay, ref],
            [
                sales_voucher("order_1", 10_000),
                credit_note("order_1", 3_000),
                receipt("UTR0001", stl.net_amount, pay.fee, pay.tax),
            ],
            [stl],
        )
        assert reconcile(ds).discrepancies == []


class TestTheSeamBetweenLayers:
    """Settlement-scope symptoms must be left unattributed for the agent."""

    def test_fee_disagreement_is_left_open_not_classified(self):
        row = payment("order_1", 10_000)
        stl = settlement([row])
        # Books assume a flat 1.75% instead of the real per-method MDR.
        ds = dataset(
            [row],
            [sales_voucher("order_1", 10_000), receipt("UTR0001", stl.net_amount, 175, row.tax)],
            [stl],
        )
        recon = reconcile(ds)
        fee_ex = [d for d in recon.discrepancies if "gateway_fee" in d.rule]
        assert len(fee_ex) == 1
        assert fee_ex[0].classification is None, "the engine must not guess the cause"
        assert fee_ex[0].confidence is None
        assert fee_ex[0] in recon.open_investigations

    def test_open_cases_are_counted_as_unresolved_not_matched(self):
        row = payment("order_1", 10_000)
        stl = settlement([row])
        ds = dataset(
            [row],
            [sales_voucher("order_1", 10_000), receipt("UTR0001", stl.net_amount, 175, row.tax)],
            [stl],
        )
        totals = reconcile(ds).totals
        assert totals["open_for_agent"] >= 1
        assert totals["resolved_by_rules"] == 0

    def test_unambiguous_faults_are_closed_by_rules(self):
        """The mirror of the above: no ambiguity means no agent involvement."""
        row = payment("order_1", 10_000)
        stl = settlement([row])
        ds = dataset([row], [receipt("UTR0001", stl.net_amount, row.fee, row.tax)], [stl])
        recon = reconcile(ds)
        assert recon.open_investigations == []
        assert recon.totals["resolved_by_rules"] >= 1


class TestSettlementReconstruction:
    def test_net_is_gross_minus_fee_tax_and_refunds(self):
        pay = payment("order_1", 10_000)
        ref = refund("order_1", 3_000)
        stl = settlement([pay, ref])
        ds = dataset(
            [pay, ref],
            [
                sales_voucher("order_1", 10_000),
                credit_note("order_1", 3_000),
                receipt("UTR0001", stl.net_amount, pay.fee, pay.tax),
            ],
            [stl],
        )
        check = reconcile(ds).settlement_checks[0]
        assert check["reconstructed_net_paise"] == 10_000 - pay.fee - pay.tax - 3_000
        assert check["reconstructed_net_paise"] == check["reported_net_paise"]
        assert check["booked"] is True

    def test_unbooked_settlement_is_reported_without_crashing(self):
        row = payment("order_1", 10_000)
        stl = settlement([row])
        ds = dataset([row], [sales_voucher("order_1", 10_000)], [stl])
        check = reconcile(ds).settlement_checks[0]
        assert check["booked"] is False
        assert check["booked_net_paise"] is None


class TestTimingAndClearing:
    def test_unsettled_payment_is_timing_not_an_exception(self):
        row = payment("order_pending", 7_000, settled=False, settlement_id=None, utr=None)
        ds = dataset([row], [sales_voucher("order_pending", 7_000)], [])
        recon = reconcile(ds)
        assert recon.discrepancies == [], "a not-yet-settled payment is not a fault"
        assert recon.totals["unsettled_records"] == 1
        assert recon.totals["unsettled_value_paise"] == 7_000

    def test_clearing_balance_explained_entirely_by_timing(self):
        row = payment("order_pending", 7_000, settled=False, settlement_id=None, utr=None)
        ds = dataset([row], [sales_voucher("order_pending", 7_000)], [])
        clearing = reconcile(ds).clearing
        assert clearing["actual_paise"] == 7_000
        assert clearing["expected_timing_paise"] == 7_000
        assert clearing["unexplained_paise"] == 0


class TestDeterminism:
    def test_same_input_yields_identical_output(self, clean_pair):
        a, b = reconcile(clean_pair), reconcile(clean_pair)
        assert a.totals == b.totals
        assert [d.to_dict() for d in a.discrepancies] == [d.to_dict() for d in b.discrepancies]

    def test_exception_ids_are_stable_and_unique(self):
        row = payment("order_1", 10_000)
        stl = settlement([row])
        ds = dataset([row], [receipt("UTR0001", stl.net_amount, 175, row.tax)], [stl])
        ids = [d.exception_id for d in reconcile(ds).discrepancies]
        assert len(ids) == len(set(ids))
        assert reconcile(ds).discrepancies[0].exception_id == ids[0]


class TestNoLedgerMutation:
    def test_reconcile_does_not_modify_the_dataset(self, clean_pair):
        """Moneta is read-only by construction; the engine must honour that too."""
        before = [v.to_rows() for v in clean_pair.vouchers]
        rows_before = [r.to_row() for r in clean_pair.settlement_rows]
        reconcile(clean_pair)
        assert [v.to_rows() for v in clean_pair.vouchers] == before
        assert [r.to_row() for r in clean_pair.settlement_rows] == rows_before
