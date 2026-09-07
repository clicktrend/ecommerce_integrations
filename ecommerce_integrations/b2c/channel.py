"""The channel account of an order (accounting doc §8b).

Two connectors feed the B2C instance, and both keep their per-shop settings on an account document:
Amazon SP Account and Shopify Account. Anything that differs per brand - the revenue account, the
sender of a mail - belongs there, not in a table inside the code: a fourth shop must be a document
someone fills in, not a deployment.
"""

import frappe

CHANNEL_FIELDS = (("amazon_account", "Amazon SP Account"), ("shopify_account", "Shopify Account"))


def account_of(so):
	"""(doctype, name) of the channel account this order came from, or (None, None)."""
	for field, doctype in CHANNEL_FIELDS:
		if so.get(field):
			return doctype, so.get(field)
	return None, None


def values(so, *fields):
	"""Read the given fields from the order's channel account; missing account or missing field
	yields an empty dict, so every caller can fall back to its own default."""
	doctype, name = account_of(so)
	if not doctype:
		return {}
	meta = frappe.get_meta(doctype)
	known = [f for f in fields if meta.get_field(f)]
	if not known:
		return {}
	return frappe.db.get_value(doctype, name, known, as_dict=True) or {}
