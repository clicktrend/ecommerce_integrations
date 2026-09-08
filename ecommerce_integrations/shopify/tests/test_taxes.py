import unittest

from ecommerce_integrations.shopify import taxes


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
