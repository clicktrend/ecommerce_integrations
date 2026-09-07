import unittest

import frappe

from ecommerce_integrations.b2c import revenue


class TestRevenueRule(unittest.TestCase):
	def test_domestic_takes_the_brand_account(self):
		self.assertEqual(revenue.account_for("Germany", "8402", "8320"), "8402")

	def test_austria_takes_its_own_account(self):
		self.assertEqual(revenue.account_for("Austria", "8402", "8320"), "8320")

	def test_austria_falls_back_when_no_account_is_configured(self):
		self.assertEqual(revenue.account_for("Austria", "8402", None), "8402")

	def test_unknown_country_is_domestic(self):
		# A missing address must not silently divert revenue to the Austrian account.
		self.assertEqual(revenue.account_for(None, "8402", "8320"), "8402")


class Invoice:
	def __init__(self, items):
		self.items = items


class TestApply(unittest.TestCase):
	def setUp(self):
		self.original = (revenue.channel_accounts, revenue.shipping_country)

	def tearDown(self):
		revenue.channel_accounts, revenue.shipping_country = self.original

	def invoice(self):
		# Not a frappe._dict: on a dict, `.items` is the built-in method, while a real Sales Invoice
		# carries the child table under that name.
		return Invoice([frappe._dict(income_account=None), frappe._dict(income_account="8200")])

	def test_writes_the_account_on_every_line(self):
		revenue.channel_accounts = lambda so: ("8402", "8320")
		revenue.shipping_country = lambda so: "Germany"
		si = self.invoice()
		self.assertEqual(revenue.apply(si, frappe._dict()), "8402")
		self.assertEqual([row.income_account for row in si.items], ["8402", "8402"])

	def test_channel_without_account_leaves_the_lines_alone(self):
		# The caller logs this; the company default then applies, visibly rather than silently.
		revenue.channel_accounts = lambda so: (None, None)
		revenue.shipping_country = lambda so: "Germany"
		si = self.invoice()
		self.assertIsNone(revenue.apply(si, frappe._dict()))
		self.assertEqual([row.income_account for row in si.items], [None, "8200"])

	def test_austria_order_books_on_the_austrian_account(self):
		revenue.channel_accounts = lambda so: ("8402", "8320")
		revenue.shipping_country = lambda so: "Austria"
		si = self.invoice()
		self.assertEqual(revenue.apply(si, frappe._dict()), "8320")
		self.assertEqual([row.income_account for row in si.items], ["8320", "8320"])
