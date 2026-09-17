"""Connections of a Sales Order: the Shopify logs behind it.

Other connector apps put their own records into the same group (found by label, created by
whichever app runs first), so the order shows all of them together without an import between
the apps. The log links the order through `sales_order`, the dashboard's standard fieldname;
shopify.utils.link_log_to_sales_order fills it.
"""

from frappe import _

GROUP_LABEL = "Sales Channel"
DOCTYPES = ("Ecommerce Integration Log",)


def sales_order(data):
	transactions = data.setdefault("transactions", [])
	label = _(GROUP_LABEL)
	group = next((g for g in transactions if g.get("label") == label), None)
	if group is None:
		group = {"label": label, "items": []}
		transactions.append(group)
	group["items"].extend(d for d in DOCTYPES if d not in group["items"])
	return data
