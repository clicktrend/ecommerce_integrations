import types
import unittest
from unittest import mock

import frappe

from ecommerce_integrations.shopify import product


def _account(name, enabled=True):
	return types.SimpleNamespace(name=name, company="_Test Company", is_enabled=lambda: enabled)


class TestProductAccount(unittest.TestCase):
	"""Two stores under one company: the order import hands its account to the product sync
	instead of the product sync looking it up again by company."""

	def setUp(self):
		# frappe.throw translates its message; there is no site here.
		patcher = mock.patch.object(product, "_", lambda text: text)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_given_account_wins_over_company_lookup(self):
		second = _account("second.myshopify.com")
		with mock.patch.object(product, "get_company_shopify_account") as lookup:
			p = product.ShopifyProduct("1", company="_Test Company", setting=second)
		self.assertIs(p.setting, second)
		lookup.assert_not_called()

	def test_company_lookup_stays_the_fallback(self):
		fallback = _account("first.myshopify.com")
		with mock.patch.object(product, "get_company_shopify_account", return_value=fallback) as lookup:
			p = product.ShopifyProduct("1", company="_Test Company")
		self.assertIs(p.setting, fallback)
		lookup.assert_called_once_with("_Test Company")

	def test_disabled_or_missing_account_refuses(self):
		with mock.patch.object(frappe, "throw", side_effect=frappe.ValidationError) as throw:
			with self.assertRaises(frappe.ValidationError):
				product.ShopifyProduct("1", company="_Test Company", setting=_account("x", enabled=False))
			with mock.patch.object(product, "get_company_shopify_account", return_value=None):
				with self.assertRaises(frappe.ValidationError):
					product.ShopifyProduct("1", company="_Test Company")
		self.assertEqual(throw.call_count, 2)

	def test_create_items_hands_the_account_through(self):
		seen = []

		class Recorder:
			def __init__(self, product_id, company=None, variant_id=None, sku=None, setting=None):
				seen.append((product_id, setting))

			def is_synced(self):
				return True

		second = _account("second.myshopify.com")
		order = {"line_items": [{"product_id": 1, "variant_id": 2, "sku": "A-1"}, {"product_id": 3}]}
		with mock.patch.object(product, "ShopifyProduct", Recorder):
			product.create_items_if_not_exist(order, company="_Test Company", setting=second)
		self.assertEqual(seen, [(1, second), (3, second)])
