import json
import unittest

import frappe

from ecommerce_integrations.b2c import gates
from ecommerce_integrations.b2c.address_check import check_lines


class TestGatesPure(unittest.TestCase):
	def row(self, item_code, props=None):
		return frappe._dict(item_code=item_code, shopify_item_properties=json.dumps(props) if props else None)

	def test_multisizer_by_sku_suffix(self):
		self.assertTrue(gates.row_needs_multisizer(self.row("11H-multisizer")))
		self.assertFalse(gates.row_needs_multisizer(self.row("11H-54")))

	def test_multisizer_by_shopify_property(self):
		# Partner ring sets on the live Shopify path carry the gauge request as a line property.
		props = [
			{"name": "Damenring Ringgröße", "value": "54 (17.2)"},
			{"name": "Herrenring Ringgröße", "value": "Multisizer zusenden"},
			{"name": "Gravurwunsch", "value": "Innengravur"},
		]
		self.assertTrue(gates.row_needs_multisizer(self.row("127HDla", props)))
		props[1]["value"] = "62 (19.7)"
		self.assertFalse(gates.row_needs_multisizer(self.row("127HDla", props)))

	def test_paid_rules(self):
		shopify = frappe._dict(grand_total=29.0, shopify_account="acc", shopify_financial_status="pending")
		self.assertFalse(gates.is_paid(shopify))
		shopify.shopify_financial_status = "paid"
		self.assertTrue(gates.is_paid(shopify))
		shopify.shopify_financial_status = "partially_refunded"
		self.assertTrue(gates.is_paid(shopify))
		# not a Shopify order (manual, Amazon later): payment is not B2C's gate here
		self.assertTrue(gates.is_paid(frappe._dict(grand_total=29.0, shopify_account=None)))
		# zero total never waits for money
		self.assertTrue(gates.is_paid(frappe._dict(grand_total=0, shopify_account="acc", shopify_financial_status="pending")))

	def test_payment_marker_wins_over_channel_status(self):
		# The channel neutral marker (b2c_payment_status) is what every channel writes; once it is
		# set, the raw Shopify status no longer decides, and a non-Shopify order can be unpaid.
		so = frappe._dict(grand_total=29.0, shopify_account="acc", shopify_financial_status="pending", b2c_payment_status="Bezahlt")
		self.assertTrue(gates.is_paid(so))
		so.b2c_payment_status = "Teilweise erstattet"
		self.assertTrue(gates.is_paid(so))
		so.b2c_payment_status = "Erstattet"
		self.assertFalse(gates.is_paid(so))
		self.assertFalse(gates.is_paid(frappe._dict(grand_total=29.0, shopify_account=None, b2c_payment_status="Offen")))

	def test_financial_status_mapping(self):
		self.assertEqual(gates.payment_status_from_financial("paid"), "Bezahlt")
		self.assertEqual(gates.payment_status_from_financial("PAID"), "Bezahlt")
		self.assertEqual(gates.payment_status_from_financial("partially_refunded"), "Teilweise erstattet")
		self.assertEqual(gates.payment_status_from_financial("refunded"), "Erstattet")
		for open_status in ("pending", "authorized", "partially_paid", "voided", "", None):
			self.assertEqual(gates.payment_status_from_financial(open_status), "Offen", open_status)


class TestAddressLines(unittest.TestCase):
	def test_german_street_with_number(self):
		ok, msg, l1, l2 = check_lines("Musterstraße 12a", "", "DE")
		self.assertTrue(ok, msg)

	def test_german_street_without_number_fails(self):
		ok, msg, _l1, _l2 = check_lines("Musterstraße", "", "DE")
		self.assertFalse(ok)
		self.assertIn("Hausnummer", msg)

	def test_french_number_first(self):
		ok, _m, _l1, _l2 = check_lines("12 rue de la Paix", "", "FR")
		self.assertTrue(ok)

	def test_unknown_country_falls_back_to_german_pattern(self):
		ok, _m, _l1, _l2 = check_lines("Hauptstrasse 5", "", "XX")
		self.assertTrue(ok)

	def test_packstation_is_normalised_to_dhl_order(self):
		ok, msg, l1, l2 = check_lines("Packstation 123", "12345678", "DE")
		self.assertTrue(ok, msg)
		self.assertEqual(l1, "12345678 Packstation 123")
		self.assertEqual(l2, "")

	def test_packstation_without_postnummer_is_reported(self):
		ok, msg, _l1, _l2 = check_lines("Packstation 123", "", "DE")
		self.assertFalse(ok)
		self.assertIn("Postnummer", msg)

	def test_packstation_outside_germany_is_a_plain_street(self):
		ok, _m, _l1, _l2 = check_lines("Packstation 123", "", "AT")
		self.assertTrue(ok)  # matches the street regex: name + number
