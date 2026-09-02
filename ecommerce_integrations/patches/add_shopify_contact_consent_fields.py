import frappe

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE


def execute():
	"""Create the marketing consent fields on Contact for sites that already run Shopify.

	setup_custom_fields() only runs when an enabled Shopify Account is saved. Without this
	patch an existing install would fail its first order import after the change, because
	the customer sync refuses to write consent without the fields.
	"""
	if not frappe.db.exists("DocType", ACCOUNT_DOCTYPE):
		return

	if not frappe.get_all(ACCOUNT_DOCTYPE, filters={"enable_shopify": 1}, limit=1):
		return

	from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import (
		setup_custom_fields,
	)

	setup_custom_fields()
