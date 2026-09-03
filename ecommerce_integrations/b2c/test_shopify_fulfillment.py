import unittest

from ecommerce_integrations.b2c import shopify_fulfillment as sf


class TestCarrierNames(unittest.TestCase):
	def test_marello_codes_map_to_shopify_carriers(self):
		self.assertEqual(sf.carrier_name("DHL"), "DHL")
		self.assertEqual(sf.carrier_name("wpost_intl_prem"), "Deutsche Post")
		self.assertEqual(sf.carrier_name("manual_shipping_dpd"), "DPD")
		self.assertEqual(sf.carrier_name("dhl_express"), "DHL Express")

	def test_unknown_text_passes_through_and_empty_is_none(self):
		self.assertEqual(sf.carrier_name(" Kurier Meier "), "Kurier Meier")
		self.assertIsNone(sf.carrier_name(""))
		self.assertIsNone(sf.carrier_name(None))


class TestFulfillmentPayload(unittest.TestCase):
	def orders(self):
		return [
			{
				"id": 11,
				"status": "open",
				"supported_actions": ["create_fulfillment", "hold"],
				"line_items": [{"id": 101, "fulfillable_quantity": 1}, {"id": 102, "fulfillable_quantity": 0}],
			},
			{"id": 12, "status": "closed", "line_items": [{"id": 103, "fulfillable_quantity": 1}]},
			{"id": 13, "status": "on_hold", "supported_actions": ["release_hold"], "line_items": [{"id": 104, "fulfillable_quantity": 1}]},
		]

	def test_only_open_orders_with_fulfillable_lines_are_used(self):
		self.assertEqual(
			sf.open_fulfillment_orders(self.orders()),
			[{"fulfillment_order_id": 11, "fulfillment_order_line_items": [{"id": 101, "quantity": 1}]}],
		)

	def test_payload_carries_tracking_and_notifies_the_customer(self):
		payload = sf.fulfillment_payload(self.orders(), "00340434161094055555", "DHL")
		self.assertEqual(payload["tracking_info"], {"number": "00340434161094055555", "company": "DHL"})
		self.assertTrue(payload["notify_customer"])
		self.assertEqual(payload["line_items_by_fulfillment_order"][0]["fulfillment_order_id"], 11)

	def test_nothing_open_means_no_payload(self):
		self.assertIsNone(sf.fulfillment_payload([{"id": 12, "status": "closed", "line_items": []}], "1", "DHL"))
