import unittest

from ecommerce_integrations.shopify.order import _is_personalized


class TestIsPersonalized(unittest.TestCase):
	"""The grid flag must say yes exactly when the buyer chose something on the line."""

	def test_an_engraving_text_counts(self):
		line = {"properties": [{"name": "Gravurtext", "value": "Anna & Ben"}]}
		self.assertEqual(_is_personalized(line), 1)

	def test_a_cart_hidden_property_is_still_a_choice(self):
		# Shopify order #10700: every real property arrived with a leading underscore.
		line = {"properties": [{"name": "_Gravurtext Clipper Box", "value": "Peddy&Tami"}]}
		self.assertEqual(_is_personalized(line), 1)

	def test_plugin_bookkeeping_and_empty_values_do_not_count(self):
		line = {
			"properties": [
				{"name": "_pplr_customization_id", "value": "abc"},
				{"name": "__pplr_line_ref", "value": "I1M81N"},
				{"name": "Gravurtext", "value": "   "},
				{"name": "", "value": "x"},
			]
		}
		self.assertEqual(_is_personalized(line), 0)

	def test_a_line_without_properties_is_not_personalized(self):
		self.assertEqual(_is_personalized({}), 0)
		self.assertEqual(_is_personalized({"properties": None}), 0)
