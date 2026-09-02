import unittest

from ecommerce_integrations.ecommerce_integrations.doctype.ecommerce_integration_log.ecommerce_integration_log import (
	link_name,
)


class _Doc:
	name = "z6wkr3-yk.myshopify.com"


class TestLogLinkName(unittest.TestCase):
	"""process_request() passes the Shopify Account document; the log's Link field needs its name."""

	def test_a_document_yields_its_name(self):
		self.assertEqual(link_name(_Doc()), "z6wkr3-yk.myshopify.com")

	def test_a_name_and_none_pass_through(self):
		self.assertEqual(link_name("z6wkr3-yk.myshopify.com"), "z6wkr3-yk.myshopify.com")
		self.assertIsNone(link_name(None))
