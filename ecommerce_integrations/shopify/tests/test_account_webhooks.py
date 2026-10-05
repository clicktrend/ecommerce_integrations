import types
import unittest
from unittest.mock import MagicMock, patch

from ecommerce_integrations.shopify.doctype.shopify_account import shopify_account
from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import ShopifyAccount


def _disabled_account(webhooks):
	return types.SimpleNamespace(
		shopify_url="shop-a.myshopify.com",
		authentication_method="Static Access Token",
		webhooks=webhooks,
		is_enabled=lambda: False,
		_get_password_safe=lambda fieldname: "wrong-token",
	)


class TestDisabledAccountWebhooks(unittest.TestCase):
	"""Disabling an account must not depend on Shopify accepting its credentials: that save is
	how wrong credentials get corrected."""

	def _handle(self, account, unregister):
		with (
			patch.object(shopify_account.connection, "unregister_webhooks", unregister),
			patch.object(shopify_account.frappe, "log_error") as log_error,
			patch.object(shopify_account.frappe, "msgprint"),
			patch.object(shopify_account, "_", lambda text: text),
		):
			ShopifyAccount._handle_webhooks(account)
		return log_error

	def test_no_registered_webhooks_means_no_call_to_shopify(self):
		unregister = MagicMock()
		account = _disabled_account(webhooks=[])
		self._handle(account, unregister)

		unregister.assert_not_called()
		self.assertEqual(account.webhooks, [])

	def test_a_failing_unregister_does_not_block_the_save(self):
		unregister = MagicMock(side_effect=Exception("401 [API] Invalid API key or access token"))
		account = _disabled_account(webhooks=[{"webhook_id": "1", "method": "orders/create"}])
		log_error = self._handle(account, unregister)

		unregister.assert_called_once_with("shop-a.myshopify.com", "wrong-token")
		log_error.assert_called_once()
		self.assertEqual(account.webhooks, [])
