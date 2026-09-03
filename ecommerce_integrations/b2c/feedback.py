"""Feedback channel Oro -> ERPNext (B2C). Oro's ErpNext connector calls these once per purchase
order: `in_production` when the RFO behind it has become an Oro order, `shipped` when that
order carries a tracking number (Marello's outgoing push), `cancelled` when Oro/Adomio dropped
the request or the order (stock, error, operator). All are idempotent; the caller is the
connector user (role "Oro Connector"), the bookkeeping runs as Administrator like the Shopify
webhooks do.
"""

import frappe

from ecommerce_integrations.b2c import gates

ROLES = ("Oro Connector", "System Manager")


def _sales_order_for(purchase_order):
	names = frappe.get_all(
		"Purchase Order Item", filters={"parent": purchase_order, "sales_order": ["is", "set"]}, pluck="sales_order"
	)
	if not names:
		frappe.throw(f"Purchase Order {purchase_order} has no sales order behind it")
	return names[0]


def _is_gauge_order(purchase_order):
	codes = frappe.get_all("Purchase Order Item", filters={"parent": purchase_order}, pluck="item_code")
	return bool(codes) and all(code == gates.MULTISIZER_ITEM for code in codes)


@frappe.whitelist()
def in_production(purchase_order):
	"""Oro accepted the purchase order (RFO converted to an order): the ring order shows
	50 % progress; the ring-gauge order changes nothing on the sales order."""
	frappe.only_for(ROLES)
	frappe.set_user("Administrator")
	sales_order = _sales_order_for(purchase_order)
	if _is_gauge_order(purchase_order):
		so = frappe.get_doc("Sales Order", sales_order)
		gates.log_gate(so, f"Ringmaß-Bestellung {purchase_order} bei Adomio angenommen")
		return {"sales_order": sales_order, "state": so.get(gates.STATE_FIELD)}
	if frappe.db.get_value("Sales Order", sales_order, gates.STATE_FIELD) in (gates.STATE_READY, gates.STATE_OPEN):
		gates.mark_in_production(sales_order)
	return {"sales_order": sales_order, "state": frappe.db.get_value("Sales Order", sales_order, gates.STATE_FIELD)}


@frappe.whitelist()
def shipped(purchase_order, tracking_number=None, carrier=None):
	"""Marello shipped: the ring order is delivered and invoiced (mark_shipped); the ring gauge
	only tells the customer that the gauge is on its way."""
	frappe.only_for(ROLES)
	frappe.set_user("Administrator")
	sales_order = _sales_order_for(purchase_order)
	so = frappe.get_doc("Sales Order", sales_order)
	if _is_gauge_order(purchase_order):
		from ecommerce_integrations.b2c.reminders import send_template

		if not so.get("b2c_gauge_mail_sent"):
			send_template(so, "B2C Multisizer versendet")
			so.db_set("b2c_gauge_mail_sent", 1, update_modified=False)
		gates.log_gate(so, f"Ringmaß versendet ({carrier or '?'} {tracking_number or ''})")
		return {"sales_order": sales_order, "gauge": True}
	result = gates.mark_shipped(sales_order, tracking_number=tracking_number, carrier=carrier)
	return {"sales_order": sales_order, **(result or {})}


@frappe.whitelist()
def cancelled(purchase_order, reason=None):
	"""Oro/Adomio cancelled the request or the order behind this purchase order: the purchase
	order is cancelled here too and the sales order parked ("Angehalten") for a person to decide -
	reorder, replace or cancel towards the customer. Nothing goes to the customer automatically
	(no auto-cancel, no refund: money stays a manual step). A ring-gauge order only leaves a note."""
	frappe.only_for(ROLES)
	frappe.set_user("Administrator")
	sales_order = _sales_order_for(purchase_order)
	result = gates.mark_cancelled_by_supplier(sales_order, purchase_order, reason=reason)
	return {"sales_order": sales_order, **(result or {})}
