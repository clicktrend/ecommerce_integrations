import types
import unittest

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	link_name,
)


class TestLogLinkName(unittest.TestCase):
	"""process_request() passes the Shopify Account document; the log's Link field needs its name."""

	def test_a_document_yields_its_name(self):
		self.assertEqual(link_name(types.SimpleNamespace(name="shop-a.myshopify.com")), "shop-a.myshopify.com")

	def test_a_name_and_none_pass_through(self):
		self.assertEqual(link_name("shop-a.myshopify.com"), "shop-a.myshopify.com")
		self.assertIsNone(link_name(None))
