"""Pure-logic tests: Indian identifiers, GST splits, tax-code validation, PDF rendering."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.core import india
from apps.company.models.tax import validate_tax_codes
from apps.company.services import tax

from .base import GSTIN, PAN, make_gstin


class IdentifierTests(SimpleTestCase):
    def test_valid_gstin_passes(self):
        self.assertIsNone(india.validate_gstin(GSTIN, PAN))

    def test_gstin_with_wrong_check_character_fails(self):
        bad = GSTIN[:-1] + ("A" if GSTIN[-1] != "A" else "B")
        self.assertIn("check character", india.validate_gstin(bad))

    def test_gstin_must_contain_company_pan(self):
        self.assertIn("PAN", india.validate_gstin(make_gstin(pan="ZZZZZ9999Z"), PAN))

    def test_gstin_unknown_state_fails(self):
        self.assertIn("state", india.validate_gstin(make_gstin(state="25")))

    def test_pan_tan_llpin_ifsc_pin_formats(self):
        self.assertTrue(india.PAN_RE.match("ABCDE1234F"))
        self.assertFalse(india.PAN_RE.match("abcde1234f"))
        self.assertTrue(india.TAN_RE.match("ABCD12345E"))
        self.assertTrue(india.LLPIN_RE.match("AAB-1234"))
        self.assertFalse(india.LLPIN_RE.match("AAB1234"))
        self.assertTrue(india.IFSC_RE.match("HDFC0001234"))
        self.assertFalse(india.IFSC_RE.match("HDFC1001234"))
        self.assertTrue(india.PIN_RE.match("560001"))
        self.assertFalse(india.PIN_RE.match("060001"))


class GstCalculationTests(SimpleTestCase):
    def test_intra_state_splits_into_cgst_and_sgst(self):
        r = tax.calculate_gst("1000", 18, supplier_state_code="29", place_of_supply_code="29")
        self.assertEqual((r["cgst"], r["sgst"], r["igst"]), (Decimal("90.00"), Decimal("90.00"), Decimal("0.00")))
        self.assertEqual(r["grand_total"], Decimal("1180.00"))
        self.assertEqual(r["supply_type"], "intra_state")

    def test_inter_state_charges_igst(self):
        r = tax.calculate_gst("1000", 18, supplier_state_code="29", place_of_supply_code="27")
        self.assertEqual((r["cgst"], r["sgst"], r["igst"]), (Decimal("0.00"), Decimal("0.00"), Decimal("180.00")))

    def test_union_territory_without_legislature_uses_utgst(self):
        r = tax.calculate_gst("1000", 18, supplier_state_code="04", place_of_supply_code="04")
        self.assertEqual((r["utgst"], r["sgst"]), (Decimal("90.00"), Decimal("0.00")))

    def test_rounding_is_half_up_to_paise(self):
        r = tax.calculate_gst("33.33", 5, supplier_state_code="29", place_of_supply_code="27")
        self.assertEqual(r["igst"], Decimal("1.67"))

    def test_composition_and_unregistered_charge_no_tax(self):
        for treatment in ("composition", "unregistered", "not_applicable"):
            r = tax.calculate_gst("1000", 18, supplier_state_code="29", place_of_supply_code="27", treatment=treatment)
            self.assertEqual(r["total_tax"], Decimal("0.00"))
            self.assertEqual(r["grand_total"], Decimal("1000.00"))

    def test_other_territory_is_always_inter_state(self):
        r = tax.calculate_gst("100", 18, supplier_state_code="97", place_of_supply_code="97")
        self.assertEqual(r["supply_type"], "inter_state")

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):
            tax.calculate_gst("100", 18, supplier_state_code="29", place_of_supply_code="55")


class TaxCodeValidationTests(SimpleTestCase):
    CURRENT, LEGACY = [0, 0.25, 3, 5, 18, 40], [12, 28]

    def check(self, codes):
        validate_tax_codes(codes, self.CURRENT, self.LEGACY)

    def test_current_rate_accepted(self):
        self.check([{"code": "GST18", "rate": 18}])

    def test_legacy_rate_needs_effective_to(self):
        with self.assertRaises(ValidationError):
            self.check([{"code": "GST28", "rate": 28}])
        self.check([{"code": "GST28", "rate": 28, "effective_to": "2025-09-21"}])

    def test_invalid_rate_duplicate_and_missing_code(self):
        for bad in ([{"code": "X", "rate": 17}], [{"code": "A", "rate": 5}, {"code": "A", "rate": 5}],
                    [{"rate": 5}], [{"code": "A", "rate": "abc"}], "nope"):
            with self.assertRaises(ValidationError):
                self.check(bad)
