"""External ids are the identity of a synced record: they must be indexed unique so a webhook
retry racing on two workers cannot create a second sales order or customer."""

import unittest

from ecommerce_integrations.shopify.constants import CUSTOMER_ID_FIELD, ORDER_ID_FIELD
from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import get_custom_fields


def field(doctype, fieldname):
	for definition in get_custom_fields()[doctype]:
		if definition["fieldname"] == fieldname:
			return definition
	raise AssertionError(f"{doctype}.{fieldname} not defined")


class TestExternalIdFields(unittest.TestCase):
	def test_sales_order_id_is_unique_data(self):
		definition = field("Sales Order", ORDER_ID_FIELD)
		self.assertEqual(definition["fieldtype"], "Data")  # unique needs a varchar, not text
		self.assertEqual(definition.get("unique"), 1)

	def test_customer_id_is_unique(self):
		definition = field("Customer", CUSTOMER_ID_FIELD)
		self.assertEqual(definition["fieldtype"], "Data")
		self.assertEqual(definition.get("unique"), 1)

	def test_documents_that_may_repeat_an_order_stay_non_unique(self):
		for doctype in ("Sales Invoice", "Delivery Note"):
			self.assertFalse(field(doctype, ORDER_ID_FIELD).get("unique"))
