"""The channel interface: registry parsing, auto-mapping of an account, the contract guard and
the shipment listeners (2026-09-08)."""

import unittest
from unittest import mock

import frappe

from ecommerce_integrations.b2c import channel


class TestRegistry(unittest.TestCase):
	def test_hook_leaves_are_unwrapped_and_defaults_applied(self):
		hooks = {
			"Amazon SP Account": {"code_field": ["channel_code"], "channel_type": ["Marketplace"]},
			"Shopify Account": {},
		}
		with mock.patch.object(frappe, "get_hooks", return_value=hooks):
			reg = channel.registry()
			self.assertEqual(reg["Amazon SP Account"], {"code_field": "channel_code", "channel_type": "Marketplace"})
			self.assertEqual(reg["Shopify Account"], {"code_field": None, "channel_type": "Webshop"})
			self.assertEqual(channel.integration_doctypes(), ["Amazon SP Account", "Shopify Account"])

	def test_no_registered_integration_is_an_empty_registry(self):
		with mock.patch.object(frappe, "get_hooks", return_value=None):
			self.assertEqual(channel.registry(), {})


class TestEnsureForIntegration(unittest.TestCase):
	def _ensure(self, registry, row, existing=None):
		inserted = []

		class FakeDoc:
			def __init__(self, values):
				self.values = values
				self.flags = frappe._dict()
				self.name = values["code"]

			def insert(self):
				inserted.append(self.values)

		with mock.patch.object(channel, "registry", return_value=registry), mock.patch.object(
			channel, "for_integration", return_value=existing
		), mock.patch.object(frappe.db, "get_value", return_value=row), mock.patch.object(
			frappe.db, "exists", return_value=False
		), mock.patch.object(frappe, "get_doc", side_effect=FakeDoc):
			name = channel.ensure_for_integration("Amazon SP Account", "Schönschmied")
		return name, inserted

	def test_code_comes_from_the_registered_code_field_and_brand_values_move_over(self):
		reg = {"Amazon SP Account": {"code_field": "channel_code", "channel_type": "Marketplace"}}
		row = frappe._dict(
			account_name="Schönschmied", channel_code="schoenschmied_amazon", company="Y&T",
			income_account="8402 - Erlöse", sender_email="rechnung@juwelier-schoenschmied.de", brand_logo=None,
		)
		name, inserted = self._ensure(reg, row)
		self.assertEqual(name, "schoenschmied_amazon")
		values = inserted[0]
		self.assertEqual(values["channel_type"], "Marketplace")
		self.assertEqual(values["address_check"], 0)  # a marketplace owns the address
		self.assertEqual(values["integration"], "Schönschmied")
		self.assertEqual(values["income_account"], "8402 - Erlöse")
		self.assertEqual(values["sender_email"], "rechnung@juwelier-schoenschmied.de")
		self.assertNotIn("brand_logo", values)

	def test_without_a_code_field_the_account_name_is_scrubbed(self):
		reg = {"Shopify Account": {"code_field": None, "channel_type": "Webshop"}}
		row = frappe._dict(company="Y&T")
		with mock.patch.object(channel, "registry", return_value=reg), mock.patch.object(
			channel, "for_integration", return_value=None
		), mock.patch.object(frappe.db, "get_value", return_value=row), mock.patch.object(
			frappe.db, "exists", return_value=False
		), mock.patch.object(frappe, "get_doc") as get_doc:
			get_doc.return_value.name = "mit_gravur"
			channel.ensure_for_integration("Shopify Account", "mit-gravur.myshopify.com")
		values = get_doc.call_args.args[0]
		self.assertEqual(values["code"], "mit_gravur")
		self.assertEqual(values["channel_name"], "mit-gravur.myshopify.com")
		self.assertEqual(values["address_check"], 1)

	def test_default_codes(self):
		self.assertEqual(channel.code_from_name("mit-bildgravur-de.myshopify.com"), "mit_bildgravur_de")
		self.assertEqual(channel.code_from_name("z6wkr3-yk.myshopify.com"), "z6wkr3_yk")
		self.assertEqual(channel.code_from_name("Schönschmied"), "schönschmied")

	def test_an_existing_channel_is_returned_untouched(self):
		name, inserted = self._ensure({"Amazon SP Account": {"code_field": None, "channel_type": "Marketplace"}}, {}, existing="x")
		self.assertEqual(name, "x")
		self.assertEqual(inserted, [])

	def test_only_registered_doctypes_get_a_channel_on_insert(self):
		with mock.patch.object(channel, "registry", return_value={"Shopify Account": {}}), mock.patch.object(
			channel, "ensure_for_integration"
		) as ensure:
			channel.on_integration_insert(frappe._dict(doctype="Customer", name="c"))
			ensure.assert_not_called()
			channel.on_integration_insert(frappe._dict(doctype="Shopify Account", name="s"))
			ensure.assert_called_once_with("Shopify Account", "s")


class TestOrderSide(unittest.TestCase):
	def test_order_without_a_channel_yields_nothing(self):
		self.assertIsNone(channel.of_order(frappe._dict()))
		self.assertEqual(channel.values(frappe._dict(), "sender_email"), {})
		self.assertFalse(channel.address_check(frappe._dict()))

	def test_values_and_flags_come_from_the_channel(self):
		doc = frappe._dict(sender_email="rechnung@mit-gravur.de", income_account=None, address_check=1)
		with mock.patch.object(frappe, "get_cached_doc", return_value=doc):
			so = frappe._dict(sales_channel="mitgravur_shopify")
			self.assertEqual(channel.values(so, "sender_email", "income_account"), {"sender_email": "rechnung@mit-gravur.de", "income_account": None})
			self.assertTrue(channel.address_check(so))

	def test_priority(self):
		self.assertTrue(channel.is_high_priority(frappe._dict(integration_priority="High")))
		self.assertFalse(channel.is_high_priority(frappe._dict(integration_priority="Normal")))
		self.assertFalse(channel.is_high_priority(frappe._dict()))


class TestContractGuard(unittest.TestCase):
	def test_order_without_a_channel_is_not_checked(self):
		channel.validate_order(frappe._dict())  # no throw

	def test_channel_without_order_id_or_payment_status_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			channel.validate_order(frappe._dict(sales_channel="x", integration_payment_status="Bezahlt"))
		with self.assertRaises(frappe.ValidationError):
			channel.validate_order(frappe._dict(sales_channel="x", integration_order_id="#1"))

	def test_complete_contract_passes_and_priority_defaults_to_normal(self):
		so = frappe._dict(sales_channel="x", integration_order_id="#1", integration_payment_status="Offen")
		channel.validate_order(so)
		self.assertEqual(so.integration_priority, "Normal")


class TestShippedListeners(unittest.TestCase):
	def test_every_listener_runs_and_a_failure_does_not_stop_the_rest(self):
		calls = []

		def ok(so, tracking_number=None, carrier=None):
			calls.append(("ok", tracking_number, carrier))
			return "F1"

		def broken(so, tracking_number=None, carrier=None):
			raise RuntimeError("shop down")

		so = mock.Mock(name="so")
		so.name = "SO-1"
		with mock.patch.object(frappe, "get_hooks", return_value=["a.broken", "b.ok"]), mock.patch.object(
			frappe, "get_attr", side_effect=lambda p: {"a.broken": broken, "b.ok": ok}[p]
		), mock.patch.object(frappe, "log_error"):
			results = channel.notify_shipped(so, tracking_number="T1", carrier="dhl")
		self.assertEqual(calls, [("ok", "T1", "dhl")])
		self.assertEqual(results, {"a.broken": None, "b.ok": "F1"})
		so.add_comment.assert_called_once()
