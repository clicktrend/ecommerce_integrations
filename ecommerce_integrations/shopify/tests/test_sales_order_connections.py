"""The Sales Order lists its Shopify logs under Connections, and its form leaves out the raw fields the
B2C app's section "Sales Channel" repeats (user 2026-09-17)."""

import json
import unittest
from unittest import mock

import frappe

from ecommerce_integrations.shopify import dashboard, utils
from ecommerce_integrations.shopify.constants import (
	EVENT_MAPPER,
	ORDER_ACCOUNT_FIELD,
	ORDER_FINANCIAL_STATUS_FIELD,
	ORDER_FULFILLMENT_ID_FIELD,
	ORDER_ID_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_PAYMENT_GATEWAY_FIELD,
	ORDER_PLACED_AT_FIELD,
	ORDER_STATUS_FIELD,
)
from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import (
	SHOWN_WITHOUT_SALES_CHANNEL,
	get_custom_fields,
)

ORDER = json.dumps({"id": 7872743375189, "order_number": 10986, "line_items": [{"id": 1}]}, indent=4)
REFUND = json.dumps({"id": 998877, "order_id": 7872743375189})


class FakeMeta:
	def __init__(self, fields=("sales_order",)):
		self.fields = fields

	def has_field(self, fieldname):
		return fieldname in self.fields


class FakeLog(frappe._dict):
	def __init__(self, meta=None, **kw):
		super().__init__(**kw)
		self.meta = meta or FakeMeta()


class TestOrderIdFromLog(unittest.TestCase):
	def test_every_order_topic_names_the_order_in_its_id(self):
		for topic, method in EVENT_MAPPER.items():
			if topic.startswith("orders/"):
				self.assertEqual(utils.order_id_from_log(method, ORDER), "7872743375189", topic)

	def test_a_refund_names_the_order_in_order_id_not_its_own_id(self):
		self.assertEqual(utils.order_id_from_log(EVENT_MAPPER["refunds/create"], REFUND), "7872743375189")

	def test_other_logs_never_point_at_an_order(self):
		for method in ("update_inventory_on_shopify", "ecommerce_integrations.shopify.oauth.generate_oauth_token", None):
			self.assertIsNone(utils.order_id_from_log(method, ORDER), method)

	def test_unreadable_or_empty_payloads_yield_nothing(self):
		method = EVENT_MAPPER["orders/create"]
		for payload in ("", None, "not json", "[1, 2]", "{}"):
			self.assertIsNone(utils.order_id_from_log(method, payload), payload)

	def test_a_payload_already_parsed_works_too(self):
		self.assertEqual(utils.order_id_from_log(EVENT_MAPPER["orders/paid"], {"id": 42}), "42")


class TestLinkLogToSalesOrder(unittest.TestCase):
	def log(self, **kw):
		return FakeLog(integration="shopify", sales_order=None, method=EVENT_MAPPER["orders/create"], request_data=ORDER, **kw)

	def test_links_the_order_found_by_its_shopify_id(self):
		doc = self.log()
		with mock.patch.object(frappe.db, "get_value", return_value="SO-SHP-2026-00179") as get_value:
			utils.link_log_to_sales_order(doc)
		self.assertEqual(doc.sales_order, "SO-SHP-2026-00179")
		get_value.assert_called_once_with("Sales Order", {ORDER_ID_FIELD: "7872743375189"}, "name")

	def test_leaves_a_linked_log_and_other_integrations_alone(self):
		linked = FakeLog(integration="shopify", sales_order="SO-1", method=EVENT_MAPPER["orders/create"], request_data=ORDER)
		other = FakeLog(integration="unicommerce", sales_order=None, method=EVENT_MAPPER["orders/create"], request_data=ORDER)
		with mock.patch.object(frappe.db, "get_value") as get_value:
			utils.link_log_to_sales_order(linked)
			utils.link_log_to_sales_order(other)
		get_value.assert_not_called()
		self.assertEqual(linked.sales_order, "SO-1")
		self.assertIsNone(other.sales_order)

	def test_a_site_without_the_column_yet_is_left_alone(self):
		# new code, old schema (deploy before migrate): the log must still save
		doc = FakeLog(meta=FakeMeta(fields=()), integration="shopify", method=EVENT_MAPPER["orders/create"], request_data=ORDER)
		with mock.patch.object(frappe.db, "get_value") as get_value:
			utils.link_log_to_sales_order(doc)
		get_value.assert_not_called()
		self.assertNotIn("sales_order", doc)

	def test_a_rolled_back_sync_stays_unlinked(self):
		doc = self.log()
		with mock.patch.object(frappe.db, "get_value", return_value=None):
			utils.link_log_to_sales_order(doc)
		self.assertIsNone(doc.sales_order)

	def test_runs_on_every_log_save(self):
		hooks = frappe.get_hooks("doc_events", app_name="ecommerce_integrations")
		self.assertIn(
			"ecommerce_integrations.shopify.utils.link_log_to_sales_order",
			hooks["Ecommerce Integration Log"]["validate"],
		)


class TestSalesOrderConnections(unittest.TestCase):
	def test_adds_the_log_to_a_channel_group(self):
		data = dashboard.sales_order(frappe._dict(fieldname="sales_order", transactions=[]))
		self.assertEqual(data["transactions"], [{"label": frappe._("Sales Channel"), "items": ["Ecommerce Integration Log"]}])
		self.assertNotIn("non_standard_fieldnames", data)

	def test_joins_a_group_another_connector_created_and_runs_twice_without_duplicates(self):
		data = {"transactions": [{"label": frappe._("Sales Channel"), "items": ["Amazon SP Order"]}]}
		data = dashboard.sales_order(dashboard.sales_order(data))
		self.assertEqual(data["transactions"], [{"label": frappe._("Sales Channel"), "items": ["Amazon SP Order", "Ecommerce Integration Log"]}])

	def test_is_registered_for_the_sales_order(self):
		hooks = frappe.get_hooks("override_doctype_dashboards", app_name="ecommerce_integrations")
		self.assertIn("ecommerce_integrations.shopify.dashboard.sales_order", hooks["Sales Order"])


class TestSalesOrderRawFields(unittest.TestCase):
	def field(self, fieldname):
		for definition in get_custom_fields()["Sales Order"]:
			if definition["fieldname"] == fieldname:
				return definition
		raise AssertionError(f"Sales Order.{fieldname} not defined")

	def test_what_the_channel_section_repeats_shows_only_without_a_channel(self):
		self.assertEqual(SHOWN_WITHOUT_SALES_CHANNEL, "eval:!doc.sales_channel")
		for fieldname in (ORDER_NUMBER_FIELD, ORDER_ACCOUNT_FIELD, ORDER_PAYMENT_GATEWAY_FIELD, ORDER_PLACED_AT_FIELD):
			definition = self.field(fieldname)
			self.assertEqual(definition.get("depends_on"), SHOWN_WITHOUT_SALES_CHANNEL, fieldname)
			self.assertFalse(definition.get("hidden"), fieldname)

	def test_shopify_only_values_stay_visible(self):
		for fieldname in (ORDER_ID_FIELD, ORDER_STATUS_FIELD, ORDER_FINANCIAL_STATUS_FIELD, ORDER_FULFILLMENT_ID_FIELD):
			self.assertFalse(self.field(fieldname).get("depends_on"), fieldname)
