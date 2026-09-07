"""Which tax template a shop order gets (accounting doc §5 decision 1).

Shopify Mit-Gravur charges 0 % VAT and sends no tax lines: prices are gross and the tax was always
computed downstream - in Marello, and from there in sevdesk, which pulled 19 % out of the gross. The
import mirrored that faithfully and pinned the "Ecommerce Integrations - Ignore" tax category, which
blocks every template. The result was invoices without a single tax row.

ERPNext attaches the company default and Tax Rules in the form JS only, so a document created on the
server carries no taxes unless the creator resolves them. This is that resolver. It is a deliberate
twin of erpnext_amazon_sp.sync.sales_order.tax_template_for - same rule order, same reasoning - kept
here so the fork does not depend on the Amazon app being installed.
"""

import frappe
from frappe.utils import getdate, nowdate

DUMMY_TAX_CATEGORY = "Ecommerce Integrations - Ignore"


def rule_matches(rule, shipping_country, customer_group, on_date):
	"""A rule pinned to one customer is never for a shop order; every other criterion must be empty
	or match. ERPNext fills the customer group with the selling default when a rule is saved, so an
	unset group on our side must not disqualify a rule that carries one."""
	if rule.get("customer"):
		return False
	if rule.get("customer_group") and customer_group and rule["customer_group"] != customer_group:
		return False
	if rule.get("billing_country") and rule["billing_country"] != shipping_country:
		return False
	if rule.get("shipping_country") and rule["shipping_country"] != shipping_country:
		return False
	if rule.get("from_date") and getdate(rule["from_date"]) > getdate(on_date):
		return False
	if rule.get("to_date") and getdate(rule["to_date"]) < getdate(on_date):
		return False
	return True


def template_for(company, shipping_country=None, customer_group=None, on_date=None):
	"""Tax Rule by shipping country first (Austria 20 %), company default second."""
	on_date = on_date or nowdate()
	rules = frappe.get_all(
		"Tax Rule",
		filters={"tax_type": "Sales", "company": company, "sales_tax_template": ["is", "set"]},
		fields=["sales_tax_template", "shipping_country", "billing_country", "customer_group",
		        "customer", "from_date", "to_date", "priority"],
	)
	matching = [r for r in rules if rule_matches(r, shipping_country, customer_group, on_date)]
	if matching:
		# A rule that names the country beats a generic one, then the higher priority.
		matching.sort(key=lambda r: (1 if r.get("shipping_country") else 0, r.get("priority") or 0),
		              reverse=True)
		return matching[0]["sales_tax_template"]
	return frappe.db.get_value(
		"Sales Taxes and Charges Template", {"company": company, "is_default": 1, "disabled": 0}, "name"
	)


def rows_of(template):
	"""The template's rows, ready for the taxes table of a document built on the server."""
	if not template:
		return []
	doc = frappe.get_cached_doc("Sales Taxes and Charges Template", template)
	return [
		{"charge_type": row.charge_type, "account_head": row.account_head, "rate": row.rate,
		 "description": row.description, "included_in_print_rate": row.included_in_print_rate,
		 "cost_center": row.cost_center}
		for row in doc.taxes
	]
