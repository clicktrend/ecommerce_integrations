import json
import unittest

import frappe

from ecommerce_integrations.b2c import gates
from ecommerce_integrations.b2c.address_check import check_lines, house_number, shipping_note


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


class TestAddressLinesShopifySplit(unittest.TestCase):
	"""Marello checks street + street2 joined (getAdressMailing); Shopify often puts the house
	number into address2. The four real patterns from the dev site, 2026-09-04."""

	def test_number_in_line_two_passes_and_stays_there(self):
		ok, msg, l1, l2 = check_lines("Dachsteinstr.", "4", "DE")
		self.assertTrue(ok, msg)
		self.assertEqual((l1, l2), ("Dachsteinstr.", "4"))  # no merge, as in Marello

	def test_number_with_letter_in_line_two(self):
		ok, msg, _l1, _l2 = check_lines("Schwammerlweg", "11a", "DE")
		self.assertTrue(ok, msg)

	def test_street_repeated_with_number_in_line_two_fails_oro_rule(self):
		# Marello's regex accepts "St.Laurentgasse St.Laurentgasse 6", Oro's converter finds no
		# house number (line 2 is not a bare number, line 1 has none) - the gate follows Oro.
		ok, msg, _l1, _l2 = check_lines("St.Laurentgasse", "St.Laurentgasse 6", "AT")
		self.assertFalse(ok)
		self.assertEqual(msg, "Hausnummer nicht erkannt")

	def test_care_of_line_first_fails_oro_rule(self):
		ok, msg, _l1, _l2 = check_lines("Kindergarten Kohlerhof", "Christmannsweg 2", "DE")
		self.assertFalse(ok)
		self.assertEqual(msg, "Hausnummer nicht erkannt")

	def test_placeholder_street_counts_as_missing(self):
		ok, msg, l1, _l2 = check_lines("Address 1", "", "DE")
		self.assertFalse(ok)
		self.assertEqual(msg, "Straße fehlt")
		self.assertEqual(l1, "Address 1")  # reported, not rewritten

	def test_placeholder_with_street_in_line_two_still_wants_line_one(self):
		# Oro would take "Address 1" as the street (number 1 at its end) - a person has to
		# move the street into line 1 first.
		ok, msg, _l1, _l2 = check_lines("Address 1", "Musterweg 3", "DE")
		self.assertFalse(ok)
		self.assertEqual(msg, "Straße fehlt")

	def test_street_without_number_still_fails(self):
		ok, msg, _l1, _l2 = check_lines("Musterstraße", "", "DE")
		self.assertFalse(ok)
		self.assertEqual(msg, "Straße/Hausnummer nicht erkannt")


class TestHouseNumberOroRule(unittest.TestCase):
	"""Port of Oro's AddressFormatter::extractHouseNumber - what the request-order converter
	will accept."""

	def test_bare_number_in_line_two_wins(self):
		self.assertEqual(house_number("Dachsteinstr.", "4", "DE"), "4")
		self.assertEqual(house_number("Hauptstr. 7", "12 b", "DE"), "12 b")

	def test_number_at_end_for_german_style(self):
		self.assertEqual(house_number("Musterstraße 12a", "", "DE"), "12a")
		self.assertEqual(house_number("Straße des 17. Juni 135", "", "DE"), "135")

	def test_number_at_start_for_number_first_countries(self):
		self.assertEqual(house_number("12 rue de la Paix", "", "FR"), "12")
		self.assertEqual(house_number("rue de la Paix 12", "", "FR"), "12")  # fallback: end

	def test_no_number(self):
		self.assertIsNone(house_number("Musterstraße", "", "DE"))
		self.assertIsNone(house_number("Hauptstr.5", "", "DE"))  # Oro wants a space before it
		self.assertIsNone(house_number("", "", "DE"))


class TestShippingNote(unittest.TestCase):
	"""What an address change after submit still reaches (dialog hint)."""

	def test_before_purchase_order_nothing_to_say(self):
		for state in (gates.STATE_OPEN, gates.STATE_WAIT_PAYMENT, gates.STATE_WAIT_SIZE, gates.STATE_ON_HOLD):
			self.assertEqual(shipping_note(state, False), "", state)

	def test_submitted_purchase_order_is_read_live_by_oro(self):
		self.assertIn("nächsten Abruf", shipping_note(gates.STATE_READY, True))
		self.assertIn("nächsten Abruf", shipping_note(gates.STATE_WAIT_SIZE, True))  # ring gauge order

	def test_taken_over_by_adomio_needs_a_service_request(self):
		for state in (gates.STATE_IN_PRODUCTION, gates.STATE_SHIPPED, gates.STATE_COMPLETED, gates.STATE_RETURN):
			self.assertIn("Adomio informieren", shipping_note(state, True), state)
