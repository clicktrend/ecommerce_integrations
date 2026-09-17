"""Existing Shopify logs get the `sales_order` link new logs receive on save
(shopify.utils.link_log_to_sales_order), so the Connections tab of an older order lists its logs too.

Only logs of order webhooks are read; a log whose order was never created stays empty.
"""

import frappe

from ecommerce_integrations.shopify.constants import MODULE_NAME, ORDER_ID_FIELD
from ecommerce_integrations.shopify.utils import ORDER_ID_KEY_BY_METHOD, sales_order_for_log


def execute():
	# patches.txt has no sections, so this runs before the doctype sync adds the column
	frappe.reload_doctype("Ecommerce Integration Log")
	if not frappe.db.has_column("Sales Order", ORDER_ID_FIELD):
		return  # Shopify fields were never set up on this site

	names = frappe.get_all(
		"Ecommerce Integration Log",
		filters={
			"integration": MODULE_NAME,
			"method": ["in", list(ORDER_ID_KEY_BY_METHOD)],
			"sales_order": ["is", "not set"],
		},
		pluck="name",
	)
	linked = 0
	for name in names:
		log = frappe.db.get_value("Ecommerce Integration Log", name, ["method", "request_data"], as_dict=True)
		sales_order = sales_order_for_log(log.method, log.request_data)
		if sales_order:
			frappe.db.set_value("Ecommerce Integration Log", name, "sales_order", sales_order, update_modified=False)
			linked += 1
	print(f"ecommerce_integrations: linked {linked} of {len(names)} Shopify order logs to their Sales Order")
