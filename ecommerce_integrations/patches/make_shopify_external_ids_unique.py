"""shopify_order_id (Sales Order) and shopify_customer_id (Customer) become unique Data fields.

Refuses to run while duplicates exist - they have to be merged by a person first; a migration
that silently drops one of two orders would be worse than the race it fixes.
"""

import frappe

from ecommerce_integrations.shopify.constants import CUSTOMER_ID_FIELD, ORDER_ID_FIELD

TARGETS = (("Sales Order", ORDER_ID_FIELD), ("Customer", CUSTOMER_ID_FIELD))


def execute():
	if not frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": ORDER_ID_FIELD}):
		return  # Shopify fields were never set up on this site

	problems = []
	for doctype, field in TARGETS:
		if not frappe.db.has_column(doctype, field):
			continue
		# empty strings would collide with each other under a unique index; NULL does not
		frappe.db.sql(f"update `tab{doctype}` set `{field}` = NULL where `{field}` = ''")
		duplicates = frappe.db.sql(
			f"select `{field}`, count(*) from `tab{doctype}` where `{field}` is not null group by `{field}` having count(*) > 1",
		)
		if duplicates:
			problems.append(f"{doctype}.{field}: " + ", ".join(f"{value} x{count}" for value, count in duplicates))
	if problems:
		frappe.throw(
			"Cannot make Shopify external ids unique while duplicates exist. Merge them first:\n" + "\n".join(problems)
		)

	from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import setup_custom_fields

	setup_custom_fields()
