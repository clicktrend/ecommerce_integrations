"""Shipping confirmation ERPNext -> Shopify for the dropship model (B2C).

There is no Delivery Note in the dropship flow, so the upstream connector never tells the
shop that an order shipped. This module creates the Shopify fulfillment when the B2C sales
order reaches "Versendet" (tracking from Oro/Marello via the feedback channel), which closes
the order in the shop and lets Shopify send its own shipping mail.

Guarded by the account's `sync_delivery_note` switch ("erzeugt Fulfillments im Shop", README
§2): off in dev, where the token is read-only anyway. Idempotent: the fulfillment id is kept
on the sales order, and fulfillment orders the shop already closed are left alone.
"""

import frappe
from frappe.utils import cint

from ecommerce_integrations.shopify.constants import ORDER_ID_FIELD

FULFILLMENT_ID_FIELD = "b2c_shopify_fulfillment_id"

# Oro/Marello shipping method codes -> the carrier name Shopify shows the customer (tracking link).
CARRIERS = {
	"dhl": "DHL",
	"dhl_intl": "DHL",
	"dhl_express": "DHL Express",
	"wpost": "Deutsche Post",
	"wpost_intl": "Deutsche Post",
	"wpost_intl_prem": "Deutsche Post",
	"post_im": "Deutsche Post",
	"mail": "Deutsche Post",
	"dpd": "DPD",
	"ups": "UPS",
	"ups_express_saver": "UPS",
	"hermes": "Hermes",
	"gls": "GLS",
}


def carrier_name(carrier):
	"""Map a Marello/Oro method code or free text to a Shopify carrier name; unknown text passes through."""
	if not carrier:
		return None
	key = str(carrier).strip().lower().replace("manual_shipping_", "")
	if key in CARRIERS:
		return CARRIERS[key]
	for code, name in CARRIERS.items():
		if key.startswith(code):
			return name
	return str(carrier).strip()


def open_fulfillment_orders(fulfillment_orders):
	"""The fulfillment orders that still accept a fulfillment (open/in progress, not on hold)."""
	result = []
	for fo in fulfillment_orders:
		status = (fo.get("status") or "").lower()
		actions = fo.get("supported_actions") or []
		if status in ("closed", "cancelled", "incomplete"):
			continue
		if actions and "create_fulfillment" not in actions:
			continue
		lines = [
			{"id": line["id"], "quantity": line.get("fulfillable_quantity") or line.get("quantity") or 0}
			for line in fo.get("line_items") or []
			if (line.get("fulfillable_quantity") or line.get("quantity") or 0) > 0
		]
		if lines:
			result.append({"fulfillment_order_id": fo["id"], "fulfillment_order_line_items": lines})
	return result


def fulfillment_payload(fulfillment_orders, tracking_number=None, carrier=None, notify_customer=True):
	"""The FulfillmentV2 body for the open fulfillment orders, or None when nothing is left to ship."""
	lines = open_fulfillment_orders(fulfillment_orders)
	if not lines:
		return None
	payload = {"line_items_by_fulfillment_order": lines, "notify_customer": bool(notify_customer)}
	if tracking_number:
		tracking = {"number": str(tracking_number)}
		company = carrier_name(carrier)
		if company:
			tracking["company"] = company
		payload["tracking_info"] = tracking
	return payload


def push_fulfillment(so, tracking_number=None, carrier=None):
	"""Create the Shopify fulfillment for a shipped B2C order. Returns the fulfillment id, or
	None with the reason logged on the order. Never raises: the shipment bookkeeping in
	ERPNext must not depend on the shop."""
	from ecommerce_integrations.b2c.gates import log_gate

	order_id = so.get(ORDER_ID_FIELD)
	account_name = so.get("shopify_account")
	if not order_id or not account_name:
		return None
	if so.get(FULFILLMENT_ID_FIELD):
		return so.get(FULFILLMENT_ID_FIELD)

	account = frappe.get_doc("Shopify Account", account_name)
	if not cint(account.get("sync_delivery_note")):
		log_gate(so, "Shopify-Fulfillment übersprungen: Schalter „sync_delivery_note“ ist aus")
		return None

	try:
		import shopify

		from ecommerce_integrations.shopify.connection import get_temp_session_context

		with get_temp_session_context(account):
			fulfillment_orders = [fo.to_dict() for fo in shopify.FulfillmentOrders.find(order_id=order_id)]
			payload = fulfillment_payload(fulfillment_orders, tracking_number, carrier)
			if payload is None:
				log_gate(so, "Shopify-Fulfillment: im Shop ist nichts mehr offen (bereits erfüllt)")
				so.db_set(FULFILLMENT_ID_FIELD, "already-fulfilled", update_modified=False)
				return None
			fulfillment = shopify.FulfillmentV2(payload)
			if not fulfillment.save():
				errors = fulfillment.errors.full_messages() if fulfillment.errors else []
				raise frappe.ValidationError("; ".join(errors) or "Shopify refused the fulfillment")
			fulfillment_id = str(fulfillment.id)
	except Exception as exc:  # the shop must never roll back the shipment
		frappe.log_error(title=f"B2C Shopify fulfillment for {so.name}", message=frappe.get_traceback())
		log_gate(so, f"Shopify-Fulfillment NICHT angelegt: {exc}")
		return None

	so.db_set(FULFILLMENT_ID_FIELD, fulfillment_id, update_modified=False)
	log_gate(so, f"Shopify-Fulfillment {fulfillment_id} angelegt ({carrier_name(carrier) or '?'} {tracking_number or ''})")
	return fulfillment_id
