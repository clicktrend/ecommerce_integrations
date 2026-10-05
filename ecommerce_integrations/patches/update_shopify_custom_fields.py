import frappe

from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import setup_custom_fields

LEGACY_SETTING_DOCTYPE = "Shopify Setting"


def execute():
	# The Shopify Setting single is gone; its stored values may still be in tabSingles on a
	# site that ran the old connector. Read them raw - the doctype's meta no longer exists.
	enabled = frappe.db.get_value(
		"Singles", {"doctype": LEGACY_SETTING_DOCTYPE, "field": "enable_shopify"}, "value"
	)
	if frappe.utils.cint(enabled):
		setup_custom_fields()
