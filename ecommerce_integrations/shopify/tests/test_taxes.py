import unittest
from unittest.mock import patch

import frappe

from ecommerce_integrations.shopify import order, taxes


class TestTaxRuleMatching(unittest.TestCase):
	"""The rule order that decides whether a shop order books 19 % or the Austrian 20 %."""

	def rule(self, **kwargs):
		base = {"customer": None, "customer_group": None, "billing_country": None,
		        "shipping_country": None, "from_date": None, "to_date": None}
		base.update(kwargs)
		return base

	def test_country_rule_matches_its_country_only(self):
		rule = self.rule(shipping_country="Austria")
		self.assertTrue(taxes.rule_matches(rule, "Austria", None, "2026-09-07"))
		self.assertFalse(taxes.rule_matches(rule, "Germany", None, "2026-09-07"))

	def test_rule_pinned_to_a_customer_is_never_used(self):
		self.assertFalse(taxes.rule_matches(self.rule(customer="Amazon"), "Germany", None, "2026-09-07"))

	def test_expired_rule_does_not_match(self):
		rule = self.rule(shipping_country="Austria", to_date="2026-01-01")
		self.assertFalse(taxes.rule_matches(rule, "Austria", None, "2026-09-07"))


class TestShippingLines(unittest.TestCase):
	"""B2C shadow 2026-09-12, finding T: the shop gives shipping away above 30 EUR, we charged it anyway.

	`"0.00"` is a string and passes a plain truthiness test, so the VERSAND line was added with rate 0 -
	and ERPNext filled in the price list rate on save (3.90 EUR in `Mit-Gravur Shopify`): 39 of 148 orders
	on prod, 155.10 EUR too much, 11 of them already invoiced."""

	def setting(self, as_item=1):
		return frappe._dict({"add_shipping_as_item": as_item, "shipping_item": "VERSAND",
		                     "warehouse": "Stores - B2C", "cost_center": "Main - B2C"})

	def charge(self, price, discounts=None, tax_lines=None):
		return {"price": price, "title": "Kostenloser Versand",
		        "discount_allocations": discounts or [], "tax_lines": tax_lines or []}

	def run_lines(self, charge, as_item=1):
		taxes, items = [], []
		order.update_taxes_with_shipping_lines(taxes, [charge], self.setting(as_item), items)
		return taxes, items

	def test_free_shipping_adds_no_shipping_line(self):
		"""Kostenloser Versand ⇒ keine VERSAND-Zeile."""
		_, items = self.run_lines(self.charge("0.00"))
		self.assertEqual(items, [])

	def test_paid_shipping_still_adds_its_line(self):
		_, items = self.run_lines(self.charge("4.90"))
		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]["item_code"], "VERSAND")
		self.assertEqual(items[0]["rate"], 4.90)

	def test_shipping_discounted_to_nothing_adds_no_line(self):
		charge = self.charge("4.90", discounts=[{"amount": "4.90"}])
		self.assertEqual(self.run_lines(charge)[1], [])

	def test_partly_discounted_shipping_keeps_what_is_left(self):
		charge = self.charge("4.90", discounts=[{"amount": "2.00"}])
		self.assertAlmostEqual(self.run_lines(charge)[1][0]["rate"], 2.90, places=2)

	def test_free_shipping_adds_no_actual_charge_either(self):
		"""Without `add_shipping_as_item` the same line would be an Actual tax row."""
		taxes, _ = self.run_lines(self.charge("0.00"), as_item=0)
		self.assertEqual(taxes, [])

	def test_tax_line_of_free_shipping_is_not_pinned_to_the_shipping_item(self):
		"""No VERSAND line means item_wise_tax_detail must not name one.

		The tax rows keep coming - they carry the tax of the goods as well - so the account lookup is
		stubbed here; this test is about which item the row is pinned to, not about the account."""
		charge = self.charge("0.00", tax_lines=[{"title": "MwSt", "rate": 0.19, "price": "0.00"}])
		with patch.object(order, "get_tax_account_head", return_value="1776 - B2C"), \
		     patch.object(order, "get_tax_account_description", return_value="MwSt 19 %"):
			taxes, items = self.run_lines(charge)
		self.assertEqual(items, [])
		self.assertEqual(len(taxes), 1)
		self.assertEqual(taxes[0]["item_wise_tax_detail"], {})

	def test_tax_line_of_paid_shipping_stays_pinned_to_the_shipping_item(self):
		charge = self.charge("4.90", tax_lines=[{"title": "MwSt", "rate": 0.19, "price": "0.78"}])
		with patch.object(order, "get_tax_account_head", return_value="1776 - B2C"), \
		     patch.object(order, "get_tax_account_description", return_value="MwSt 19 %"):
			taxes, items = self.run_lines(charge)
		self.assertEqual(len(items), 1)
		self.assertEqual(taxes[0]["item_wise_tax_detail"], {"VERSAND": [19.0, 0.78]})
