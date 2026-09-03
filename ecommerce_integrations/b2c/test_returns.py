import unittest

from ecommerce_integrations.b2c import returns


class TestRefundReading(unittest.TestCase):
	def test_amount_prefers_successful_refund_transactions(self):
		refund = {
			"transactions": [
				{"kind": "refund", "status": "success", "amount": "12.50"},
				{"kind": "refund", "status": "failure", "amount": "99.00"},
				{"kind": "sale", "status": "success", "amount": "5.00"},
			],
			"refund_line_items": [{"subtotal": "1.00", "total_tax": "0.19"}],
		}
		self.assertAlmostEqual(returns.refund_amount(refund), 12.5)

	def test_amount_falls_back_to_line_subtotals(self):
		refund = {"transactions": [], "refund_line_items": [{"subtotal": "10.00", "total_tax": "1.90"}]}
		self.assertAlmostEqual(returns.refund_amount(refund), 11.9)

	def test_refunded_quantities_are_summed_per_shop_line(self):
		refund = {
			"refund_line_items": [
				{"quantity": 1, "line_item": {"sku": "PS195", "variant_id": 1, "product_id": 2}},
				{"quantity": 2, "line_item": {"sku": "PS195", "variant_id": 1, "product_id": 2}},
				{"quantity": 0, "line_item": {"sku": "PSBox1", "variant_id": 3, "product_id": 4}},
			]
		}
		self.assertEqual(returns.refunded_quantities(refund), {("PS195", 1, 2): 3})


class TestRowMatching(unittest.TestCase):
	def test_return_rows_with_negative_quantities_match_once(self):
		import frappe

		rows = [frappe._dict(item_code="168HH", qty=-1), frappe._dict(item_code="168HH", qty=-1)]
		refund = {"refund_line_items": [{"quantity": 1, "line_item": {"sku": "168HH"}}]}
		matched = returns.match_rows(rows, refund)
		self.assertEqual([(id(row), qty) for row, qty in matched], [(id(rows[0]), 1)])
