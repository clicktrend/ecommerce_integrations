"""PIM-first order lines (B2C-PIM plan §6 E4): the hub's mapper is asked before anything is
created, the channel's policy decides the fallback, item defaults come from the hub - all by hook
name, all against mocks. Deliberately on unittest.TestCase (the shared TestCase base is dead)."""

import types
import unittest
from unittest.mock import patch

import frappe

from ecommerce_integrations.shopify import product as product_module
from ecommerce_integrations.shopify.product import ShopifyProduct, create_items_if_not_exist


def _setting(name="z6wkr3-yk.myshopify.com", company="Yücel & Tirgil GbR"):
	s = types.SimpleNamespace(name=name, company=company, warehouse=None)
	s.is_enabled = lambda: True
	return s


ORDER = {"line_items": [{"product_id": 111, "variant_id": 222, "sku": "105Dla-50"}]}


class TestHubSeam(unittest.TestCase):
	def test_without_hub_hooks_everything_is_none_or_empty(self):
		with patch.object(frappe, "get_hooks", return_value=None):
			self.assertIsNone(product_module.resolve_hub_item(_setting(), sku="x"))
			self.assertEqual(product_module.hub_item_defaults(_setting()), {})
			self.assertIsNone(product_module.record_hub_item(_setting(), "RE105D-50", "105Dla-50"))

	def test_hooks_are_called_by_name_with_the_account(self):
		seen = []

		def attr(fn):
			return lambda *a, **k: seen.append((fn, a, k)) or "RE105D-50"

		with patch.object(frappe, "get_hooks", side_effect=lambda h: [f"hub.{h}"]), patch.object(frappe, "get_attr", side_effect=attr):
			self.assertEqual(product_module.resolve_hub_item(_setting(), sku="105Dla-50", product_id=111, variant_id=222), "RE105D-50")
		fn, args, kwargs = seen[0]
		self.assertEqual(fn, "hub.sales_channel_item_resolver")
		self.assertEqual(args, ("Shopify Account", "z6wkr3-yk.myshopify.com"))
		self.assertEqual(kwargs, {"sku": "105Dla-50", "product_id": 111, "variant_id": 222})


class TestCreateItemsIfNotExist(unittest.TestCase):
	def _run(self, resolved, defaults, synced=False):
		calls = {"link": [], "record": [], "sync": 0}
		fake = types.SimpleNamespace(setting=_setting(), hub_defaults=defaults)
		fake.is_synced = lambda: synced
		fake.sync_product = lambda: calls.__setitem__("sync", calls["sync"] + 1)
		with patch.object(product_module, "ShopifyProduct", return_value=fake), patch.object(
			product_module, "resolve_hub_item", return_value=resolved
		), patch.object(product_module, "link_existing_item", side_effect=lambda *a, **k: calls["link"].append(a)), patch.object(
			product_module, "record_hub_item", side_effect=lambda *a, **k: calls["record"].append(a)
		), patch.object(product_module.ecommerce_item, "get_erpnext_item_code", return_value="10208130433365"):
			create_items_if_not_exist(ORDER, company="c", setting=fake.setting)
		return calls

	def test_hub_hit_links_and_records_but_never_syncs(self):
		calls = self._run(resolved="RE105D-50", defaults={})
		self.assertEqual(calls["sync"], 0)
		self.assertEqual(calls["link"][0][:2], ("RE105D-50", 111))
		self.assertEqual(calls["record"][0][1:3], ("RE105D-50", "105Dla-50"))

	def test_reject_policy_stops_the_import_loudly(self):
		with self.assertRaises(frappe.ValidationError):
			self._run(resolved=None, defaults={"missing_item_policy": "reject"})

	def test_create_policy_syncs_and_records_the_created_item(self):
		calls = self._run(resolved=None, defaults={"missing_item_policy": "create_flagged"})
		self.assertEqual(calls["sync"], 1)
		self.assertEqual(calls["record"][0][1:3], ("10208130433365", "105Dla-50"))

	def test_already_synced_lines_are_left_alone(self):
		calls = self._run(resolved="RE105D-50", defaults={}, synced=True)
		self.assertEqual((calls["sync"], calls["link"], calls["record"]), (0, [], []))


class TestItemGroupAndDefaults(unittest.TestCase):
	def _product(self, defaults):
		p = ShopifyProduct.__new__(ShopifyProduct)
		p.setting = _setting()
		p.company = "c"
		p._hub_defaults = defaults
		return p

	def test_no_item_group_is_created_from_free_text(self):
		p = self._product({})
		with patch.object(frappe.db, "exists", return_value=False), patch.object(frappe.db, "get_value", return_value=None), patch.object(
			product_module, "get_root_of", return_value="All Item Groups"
		), patch.object(frappe, "get_doc") as get_doc:
			self.assertEqual(p._get_item_group("Fuellfederhalter"), "All Item Groups")
		get_doc.assert_not_called()

	def test_channel_default_group_wins_over_the_product_type(self):
		p = self._product({"item_group": "Schreibgeräte"})
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe.db, "get_value", return_value="Füller"):
			self.assertEqual(p._get_item_group("Füller", "Schreibgeräte"), "Schreibgeräte")

	def test_created_item_carries_the_channel_defaults(self):
		p = self._product(
			{"is_stock_item": 0, "delivered_by_supplier": 1, "brand": "Mit-Gravur", "item_tax_template": "DE 19", "pim_missing_field": "custom_pim_missing", "item_group": "Ringe"}
		)
		captured = {}
		with patch.object(product_module, "_match_sku_and_link_item", return_value=True), patch.object(
			frappe.db, "exists", return_value=True
		), patch.object(product_module, "_item_code", return_value="105Dla-50"), patch.object(product_module, "_get_item_image", return_value=None):
			def match(item_dict, *a, **k):
				captured.update(item_dict)
				return True

			with patch.object(product_module, "_match_sku_and_link_item", side_effect=match):
				p._create_item({"id": 111, "title": "Feliz", "variants": [{"price": "19.90"}], "weight_unit": "g", "variant_id": 222, "sku": "105Dla-50"}, None)
		self.assertEqual((captured["is_stock_item"], captured["delivered_by_supplier"]), (0, 1))
		self.assertEqual(captured["brand"], "Mit-Gravur")
		self.assertEqual(captured["taxes"], [{"item_tax_template": "DE 19"}])
		self.assertEqual(captured["custom_pim_missing"], 1)
		self.assertEqual(captured["item_group"], "Ringe")


class TestSoldSkuOnTheLine(unittest.TestCase):
	def test_get_order_items_writes_the_sold_sku(self):
		from ecommerce_integrations.shopify import order as order_module

		line = {"product_exists": True, "product_id": 111, "variant_id": 222, "sku": " 105HDla ", "name": "Feliz", "quantity": 1, "price": "29.90", "properties": []}
		with patch.object(order_module, "get_item_code", return_value="RP105HD"), patch.object(order_module, "_get_item_price", return_value=29.9), patch.object(
			order_module, "_get_total_discount", return_value=0
		):
			items = order_module.get_order_items([line], _setting(), "2026-09-10", taxes_inclusive=True)
		self.assertEqual(items[0]["item_code"], "RP105HD")
		self.assertEqual(items[0]["shopify_sku"], "105HDla")

