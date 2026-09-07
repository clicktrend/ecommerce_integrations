"""Two shops under one company: the import hands its account through instead of looking it up
by company, and the live pull without an account covers every enabled one (2026-09-07)."""

import unittest
from unittest import mock

import frappe

from ecommerce_integrations.b2c import live_pull
from ecommerce_integrations.shopify import product


class FakeSetting:
	def __init__(self, name, enabled=True, company="Y&T"):
		self.name = name
		self.enabled = enabled
		self.company = company

	def is_enabled(self):
		return self.enabled


class TestProductAccount(unittest.TestCase):
	def test_given_account_wins_over_company_lookup(self):
		bildgravur = FakeSetting("mit-bildgravur-de.myshopify.com")
		with mock.patch.object(product, "get_company_shopify_account") as lookup:
			p = product.ShopifyProduct("4617076998282", company="Y&T", sku="AH440bi-2", setting=bildgravur)
		self.assertIs(p.setting, bildgravur)
		lookup.assert_not_called()

	def test_company_lookup_stays_the_fallback(self):
		fallback = FakeSetting("z6wkr3-yk.myshopify.com")
		with mock.patch.object(product, "get_company_shopify_account", return_value=fallback) as lookup:
			p = product.ShopifyProduct("1", company="Y&T")
		self.assertIs(p.setting, fallback)
		lookup.assert_called_once_with("Y&T")

	def test_disabled_or_missing_account_refuses(self):
		with self.assertRaises(frappe.ValidationError):
			product.ShopifyProduct("1", company="Y&T", setting=FakeSetting("x", enabled=False))
		with mock.patch.object(product, "get_company_shopify_account", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				product.ShopifyProduct("1", company="Y&T")

	def test_create_items_hands_the_account_through(self):
		seen = []

		class Recorder:
			def __init__(self, product_id, company=None, variant_id=None, sku=None, setting=None):
				seen.append((product_id, company, variant_id, sku, setting))

			def is_synced(self):
				return True

		account = FakeSetting("mit-bildgravur-de.myshopify.com")
		order = {"line_items": [{"product_id": 1, "variant_id": 2, "sku": "SA49bb"}, {"product_id": 3, "sku": None}]}
		with mock.patch.object(product, "ShopifyProduct", Recorder):
			product.create_items_if_not_exist(order, company="Y&T", setting=account)
		self.assertEqual(seen, [(1, "Y&T", 2, "SA49bb", account), (3, "Y&T", None, None, account)])


class TestLivePullAllAccounts(unittest.TestCase):
	def test_without_account_every_enabled_one_is_pulled(self):
		calls = []

		def fake_pull_account(name, minutes=None, dry_run=False):
			calls.append((name, minutes, dry_run))
			if name == "broken.myshopify.com":
				raise RuntimeError("token expired")
			return {"account": name, "created": 1}

		names = ["a.myshopify.com", "broken.myshopify.com", "z.myshopify.com"]
		with mock.patch.object(live_pull, "enabled_accounts", return_value=names), mock.patch.object(
			live_pull, "pull_account", side_effect=fake_pull_account
		), mock.patch.object(frappe, "log_error"):
			result = live_pull.pull(minutes=30)
		self.assertEqual([c[0] for c in calls], names)
		self.assertEqual(result["a.myshopify.com"]["created"], 1)
		self.assertIn("token expired", result["broken.myshopify.com"]["error"])
		self.assertEqual(result["z.myshopify.com"]["account"], "z.myshopify.com")  # the failure did not stop the loop

	def test_explicit_account_pulls_only_that_one(self):
		with mock.patch.object(live_pull, "pull_account", return_value={"account": "a"}) as one, mock.patch.object(
			live_pull, "enabled_accounts"
		) as listing:
			self.assertEqual(live_pull.pull("a", minutes=5), {"account": "a"})
		one.assert_called_once_with("a", minutes=5, dry_run=False)
		listing.assert_not_called()

	def test_no_enabled_account_is_an_error(self):
		with mock.patch.object(live_pull, "enabled_accounts", return_value=[]):
			with self.assertRaises(frappe.ValidationError):
				live_pull.pull()
