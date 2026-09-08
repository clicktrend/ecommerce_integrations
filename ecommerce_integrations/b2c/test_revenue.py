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


class TestChannelValues(unittest.TestCase):
	"""channel.values() reads the Sales Channel of the order (read contract); the callers
	(revenue account, mail sender) must survive an order without a channel."""

	def test_order_without_a_channel_yields_nothing(self):
		from ecommerce_integrations.b2c import channel

		self.assertIsNone(channel.of_order(frappe._dict()))
		self.assertEqual(channel.values(frappe._dict(), "sender_email"), {})

	def test_accounts_come_from_the_sales_channel(self):
		from unittest import mock

		from ecommerce_integrations.b2c import channel

		sales_channel = frappe._dict(income_account="8402", income_account_at="8320")
		with mock.patch.object(frappe, "get_cached_doc", return_value=sales_channel) as get:
			so = frappe._dict(sales_channel="schoenschmied_amazon")
			self.assertEqual(revenue.channel_accounts(so), ("8402", "8320"))
		get.assert_called_with("Sales Channel", "schoenschmied_amazon")


class TestPaymentGatewayMapping(unittest.TestCase):
	"""The gateways the connectors actually write (measured on b2c.local: Amazon 209, paypal 77,
	shopify_payments 65, Bank Deposit 8) must all find a Mode of Payment."""

	def test_known_gateways(self):
		from ecommerce_integrations.b2c import payments

		self.assertEqual(payments.mode_for("Amazon"), "Amazon")
		self.assertEqual(payments.mode_for("paypal"), "PayPal Mit Gravur")
		self.assertEqual(payments.mode_for("shopify_payments"), "Shopify Payments")
		self.assertEqual(payments.mode_for("Bank Deposit"), "Vorkasse")

	def test_unknown_gateway_is_not_guessed(self):
		from ecommerce_integrations.b2c import payments

		self.assertIsNone(payments.mode_for("klarna"))
		self.assertIsNone(payments.mode_for(None))


class TestTaxRuleMatching(unittest.TestCase):
	"""The rule order that decides whether a shop order books 19 % or the Austrian 20 %."""

	def rule(self, **kwargs):
		base = {"customer": None, "customer_group": None, "billing_country": None,
		        "shipping_country": None, "from_date": None, "to_date": None}
		base.update(kwargs)
		return base

	def test_country_rule_matches_its_country_only(self):
		from ecommerce_integrations.b2c import taxes

		rule = self.rule(shipping_country="Austria")
		self.assertTrue(taxes.rule_matches(rule, "Austria", None, "2026-09-07"))
		self.assertFalse(taxes.rule_matches(rule, "Germany", None, "2026-09-07"))

	def test_rule_pinned_to_a_customer_is_never_used(self):
		from ecommerce_integrations.b2c import taxes

		self.assertFalse(taxes.rule_matches(self.rule(customer="Amazon"), "Germany", None, "2026-09-07"))

	def test_expired_rule_does_not_match(self):
		from ecommerce_integrations.b2c import taxes

		rule = self.rule(shipping_country="Austria", to_date="2026-01-01")
		self.assertFalse(taxes.rule_matches(rule, "Austria", None, "2026-09-07"))
