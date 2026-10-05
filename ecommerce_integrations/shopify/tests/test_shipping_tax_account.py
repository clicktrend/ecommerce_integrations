import types
import unittest
from unittest.mock import MagicMock, patch

from ecommerce_integrations.shopify import order


class TestShippingTaxAccount(unittest.TestCase):
	"""Every order with a shipping line died in update_taxes_with_shipping_lines() with
	"get_tax_account_head() missing 1 required positional argument: 'setting'"."""

	def test_shipping_charge_and_its_tax_resolve_against_the_account(self):
		account = types.SimpleNamespace(add_shipping_as_item=0, shipping_item=None, cost_center="Main - TC")
		shipping_lines = [
			{
				"title": "Standard",
				"price": "4.90",
				"discount_allocations": [],
				"tax_lines": [{"title": "VAT", "price": "0.78", "rate": 0.19}],
			}
		]
		head = MagicMock(return_value="Freight - TC")
		taxes = []
		with (
			patch.object(order, "get_tax_account_head", head),
			patch.object(order, "get_tax_account_description", return_value=None),
		):
			order.update_taxes_with_shipping_lines(taxes, shipping_lines, account, items=[], taxes_inclusive=True)

		self.assertEqual(len(taxes), 2)
		for call in head.call_args_list:
			self.assertIs(call.args[1], account)
		self.assertEqual([c.kwargs["charge_type"] for c in head.call_args_list], ["shipping", "sales_tax"])
