"""The arithmetic that creates money.

Every rupee figure in the system is derived from these three functions, so a defect
here is silently wrong everywhere. In particular the half-up rounding is not
decoration: Python's built-in round() rounds half to *even*, and using it would
manufacture exactly the sub-rupee drifts this project exists to explain.
"""

from decimal import Decimal

import pytest

from moneta.money import bps_of, gst_on, round_half_up, rupees, to_paise


class TestRoundHalfUp:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("0.5", 1),  # banker's rounding would give 0
            ("1.5", 2),
            ("2.5", 3),  # banker's rounding would give 2
            ("3.5", 4),
            ("-0.5", -1),
            ("-1.5", -2),
            ("0.4999", 0),
            ("0.5001", 1),
        ],
    )
    def test_halves_round_away_from_zero(self, value, expected):
        assert round_half_up(Decimal(value)) == expected

    def test_differs_from_builtin_round_on_even_halves(self):
        # The whole reason this function exists. If this ever passes with `round`,
        # someone has changed the rounding mode.
        assert round_half_up(Decimal("2.5")) == 3
        assert round(Decimal("2.5")) == 2


class TestBpsOf:
    def test_upi_is_zero_rated(self):
        assert bps_of(1_000_00, 0) == 0

    def test_two_percent_of_a_round_amount(self):
        assert bps_of(1_000_00, 200) == 20_00

    def test_rounds_half_up_not_truncating(self):
        # 12345 paise at 200 bps = 246.9 paise -> 247, not 246.
        assert bps_of(12_345, 200) == 247

    def test_zero_amount(self):
        assert bps_of(0, 200) == 0

    def test_result_is_always_an_int(self):
        assert isinstance(bps_of(9_999, 175), int)


class TestGstOn:
    def test_eighteen_percent(self):
        assert gst_on(100_00) == 18_00

    def test_rounds_half_up(self):
        # 25 paise at 18% = 4.5 -> 5.
        assert gst_on(25) == 5

    def test_per_transaction_rounding_can_differ_from_aggregate(self):
        """The GST_INPUT_ROUNDING_DRIFT fault, reduced to its essence.

        Razorpay rounds GST per transaction; a merchant computing 18% of the summed
        fee gets a different total. Summing the parts must not equal taxing the sum,
        or the exception class the agent detects would not exist.
        """
        fees = [25, 25, 25]
        per_transaction = sum(gst_on(f) for f in fees)
        on_aggregate = gst_on(sum(fees))
        assert per_transaction == 15
        assert on_aggregate == 14
        assert per_transaction != on_aggregate


class TestToPaise:
    @pytest.mark.parametrize(
        "value,expected",
        [("1", 100), ("1.50", 150), ("0.01", 1), ("1234.56", 123456), ("0", 0)],
    )
    def test_rupee_strings_convert_exactly(self, value, expected):
        assert to_paise(value) == expected

    def test_float_input_does_not_drift(self):
        # 1.15 is not exactly representable as a float; going through str() first
        # is what keeps this from landing on 114.
        assert to_paise(1.15) == 115


class TestRupees:
    @pytest.mark.parametrize(
        "paise,expected",
        [
            (0, "₹0.00"),
            (1, "₹0.01"),
            (100, "₹1.00"),
            (123456, "₹1,234.56"),
            (100000000, "₹1,000,000.00"),
        ],
    )
    def test_formatting(self, paise, expected):
        assert rupees(paise) == expected

    def test_negative_sign_precedes_the_symbol(self):
        assert rupees(-51317) == "-₹513.17"

    def test_paise_are_always_two_digits(self):
        assert rupees(105) == "₹1.05"
        assert rupees(150) == "₹1.50"
