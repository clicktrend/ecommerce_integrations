import unittest
from datetime import date, timedelta
from unittest.mock import patch

from frappe.utils import getdate, nowdate

from ecommerce_integrations.shopify.order import _delivery_date, get_order_items


class _Setting:
	warehouse = "Stores - Test"


class TestOrderGuards(unittest.TestCase):
	"""Two failures the replay of stored orders surfaced on 2026-09-02."""

	def test_a_line_without_resolvable_item_fails_the_whole_order(self):
		lines = [
			{"id": 1, "product_exists": True, "sku": "PS163-25", "variant_id": 1, "quantity": 1, "price": "1", "tax_lines": [], "name": "a"},
			{"id": 2, "product_exists": True, "sku": "PS163_PS175_MIX-25", "variant_id": 2, "quantity": 1, "price": "1", "tax_lines": [], "name": "b"},
		]
		with patch("ecommerce_integrations.shopify.order.get_item_code", side_effect=["PS163-25", None]):
			self.assertEqual(get_order_items(lines, _Setting(), date.today(), taxes_inclusive=True), [])

	def test_delivery_date_is_the_order_date_but_never_in_the_past(self):
		today = getdate(nowdate())
		self.assertEqual(_delivery_date((today - timedelta(days=10)).isoformat()), today)
		self.assertEqual(_delivery_date((today + timedelta(days=2)).isoformat()), today + timedelta(days=2))
		self.assertEqual(_delivery_date(None), today)
