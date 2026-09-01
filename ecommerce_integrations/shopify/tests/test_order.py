# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ecommerce_integrations.shopify.constants import ORDER_ITEM_PROPERTIES_FIELD
from ecommerce_integrations.shopify.order import get_order_items, sync_sales_order


class TestOrder(IntegrationTestCase):
	def test_sync_with_variants(self):
		pass

	@patch("ecommerce_integrations.shopify.order.get_item_code", return_value="_Test Item")
	def test_line_item_properties_are_carried_verbatim(self, _):
		# Engraving data arrives as line item properties; it has to survive the
		# import unparsed, because it is interpreted downstream and not here.
		engraved = [{"name": "Gravurtext", "value": "Für Anna & Ben"}, {"name": "Schrift", "value": "7"}]
		line_items = [
			{
				"name": "Engraved Bracelet",
				"quantity": 1,
				"price": "29.90",
				"product_exists": True,
				"properties": engraved,
			},
			# a line without personalisation must not carry an empty payload
			{"name": "Plain Bracelet", "quantity": 1, "price": "19.90", "product_exists": True},
		]

		items = get_order_items(
			line_items, frappe._dict(warehouse="_Test Warehouse"), delivery_date=None, taxes_inclusive=False
		)

		self.assertEqual(json.loads(items[0][ORDER_ITEM_PROPERTIES_FIELD]), engraved)
		self.assertIsNone(items[1][ORDER_ITEM_PROPERTIES_FIELD])
