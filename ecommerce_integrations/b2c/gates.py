"""Gates and states of the B2C order workflow (Frappe Workflow "B2C Auftrag" on Sales Order).

Automatic transitions are written straight to the workflow state field (frappe's
validate_workflow only allows role-gated transitions on save); the Workflow document holds the
manual actions people take (payment received, ring size entered, address confirmed, hold,
resume, archive, return). "Produktionsbereit" is the moment the purchase order to Adomio is
created AND submitted - the Oro connector reads only submitted purchase orders.
"""

import json

import frappe
from frappe.utils import cint, flt, nowdate

from ecommerce_integrations.b2c.address_check import check_address

WORKFLOW_NAME = "B2C Auftrag"
STATE_FIELD = "workflow_state"

STATE_DRAFT = "Entwurf"
STATE_OPEN = "Offen"
STATE_WAIT_PAYMENT = "Warten auf Zahlung"
STATE_WAIT_SIZE = "Warten auf Ringgröße"
STATE_ADDRESS = "Adressprüfung"
STATE_READY = "Produktionsbereit"
STATE_IN_PRODUCTION = "In Produktion"
STATE_SHIPPED = "Versendet"
STATE_COMPLETED = "Abgeschlossen"
STATE_ON_HOLD = "Angehalten"
STATE_WAIT_FEEDBACK = "Warten auf Kundenrückmeldung"
STATE_ARCHIVED = "Archiviert"
STATE_RETURN = "Retoure"
STATE_CANCELLED = "Storniert"

# States the gate evaluation may act on. Everything else is either downstream of the release
# or parked by a human and must not be moved by automation.
GATE_STATES = (STATE_OPEN, STATE_WAIT_PAYMENT, STATE_WAIT_SIZE, STATE_ADDRESS, None, "")

PAID_STATUSES = ("paid", "partially_refunded")
MULTISIZER_ITEM = "multisizer"
MULTISIZER_SUFFIX = "-multisizer"
MULTISIZER_VALUE = "multisizer"


def log_gate(so, text):
	so.add_comment("Info", f"B2C-Workflow: {text}")


def set_state(so, state, note=None):
	"""Automatic transition: bypasses the role check on purpose (see module docstring)."""
	if so.get(STATE_FIELD) == state:
		return
	so.db_set(STATE_FIELD, state, update_modified=False)
	log_gate(so, f"→ {state}" + (f" ({note})" if note else ""))


def is_shopify(so):
	return bool(so.get("shopify_account"))


def is_paid(so):
	"""Payment is B2C's responsibility. Shopify tells the status; anything that is not a
	Shopify order (manual, Amazon later) counts as paid, and so does a zero total."""
	if flt(so.grand_total) <= 0.01:
		return True
	if not is_shopify(so):
		return True
	return (so.get("shopify_financial_status") or "").lower() in PAID_STATUSES


def item_properties(row):
	raw = row.get("shopify_item_properties")
	if not raw:
		return []
	try:
		data = json.loads(raw)
	except (TypeError, ValueError):
		return []
	return data if isinstance(data, list) else []


def row_needs_multisizer(row):
	"""Two conventions: Marello's SKU suffix "-multisizer" and the Shopify line property
	"… Ringgröße: Multisizer zusenden" (partner ring sets). Both mean: send the ring gauge first,
	the size arrives later by mail and is entered into the property."""
	if (row.item_code or "").lower().endswith(MULTISIZER_SUFFIX):
		return True
	for prop in item_properties(row):
		name = str(prop.get("name") or "").lower()
		value = str(prop.get("value") or "").lower()
		if "ringgr" in name and value.startswith(MULTISIZER_VALUE):
			return True
	return False


def needs_multisizer(so):
	return any(row_needs_multisizer(row) for row in so.items if row.item_code != MULTISIZER_ITEM)


def needs_address_check(so):
	return is_shopify(so) and not cint(so.get("b2c_address_confirmed"))


def dropship_rows(so):
	return [row for row in so.items if row.delivered_by_supplier and row.supplier]


def ordered_row_names(so):
	"""SO item rows that already sit on a live purchase order (docstatus < 2)."""
	rows = frappe.get_all(
		"Purchase Order Item",
		filters={"sales_order": so.name, "docstatus": ["<", 2]},
		pluck="sales_order_item",
	)
	return {r for r in rows if r}


def ensure_dropship_po(so):
	"""Create AND submit the purchase order(s) for every dropship line not ordered yet.
	make_purchase_order() inserts the documents itself (one per supplier)."""
	from erpnext.selling.doctype.sales_order.sales_order import make_purchase_order

	already = ordered_row_names(so)
	selected = [
		{"item_code": row.item_code, "supplier": row.supplier}
		for row in dropship_rows(so)
		if row.name not in already and row.item_code != MULTISIZER_ITEM
	]
	if not selected:
		return []
	created = []
	for po in make_purchase_order(so.name, selected_items=selected):
		if not po.name:
			continue
		if po.docstatus == 0:
			po.submit()
		created.append(po.name)
	log_gate(so, f"Bestellung an Adomio eingereicht: {', '.join(created)}")
	return created


def multisizer_po_exists(so):
	return bool(
		frappe.db.exists(
			"Purchase Order Item",
			{"sales_order": so.name, "item_code": MULTISIZER_ITEM, "docstatus": ["<", 2]},
		)
	)


def ensure_multisizer_po(so):
	"""The ring gauge goes out at once, as its own purchase order (Marello's "-multi" order):
	item "multisizer", one piece, rate 0, dropshipped by letter to the end customer. The line
	links back to the sales order (the Oro connector needs that) but not to a sales order item."""
	if multisizer_po_exists(so):
		return None
	supplier = next((row.supplier for row in dropship_rows(so)), None)
	if not supplier:
		return None
	po = frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"company": so.company,
			"supplier": supplier,
			"transaction_date": nowdate(),
			"schedule_date": nowdate(),
			"customer": so.customer,
			"customer_name": so.customer_name,
			"shipping_address": so.shipping_address_name,
			"shipping_address_display": so.shipping_address,
			"items": [
				{
					"item_code": MULTISIZER_ITEM,
					"qty": 1,
					"rate": 0,
					"schedule_date": nowdate(),
					"warehouse": so.set_warehouse or so.items[0].warehouse,
					"delivered_by_supplier": 1,
					"sales_order": so.name,
				}
			],
		}
	)
	po.flags.ignore_mandatory = True
	po.insert()
	po.submit()
	log_gate(so, f"Multisizer-Bestellung an Adomio eingereicht: {po.name}")
	return po.name


def evaluate(sales_order, trigger=None):
	"""Run the gates for one submitted sales order and move it to the state that follows.
	Safe to call repeatedly: every step is idempotent."""
	so = frappe.get_doc("Sales Order", sales_order)
	if so.docstatus != 1:
		return so.get(STATE_FIELD)
	if so.get(STATE_FIELD) not in GATE_STATES:
		return so.get(STATE_FIELD)

	if not is_paid(so):
		set_state(so, STATE_WAIT_PAYMENT, so.get("shopify_financial_status"))
		if not cint(so.get("b2c_payment_request_sent")):
			from ecommerce_integrations.b2c.reminders import send_contact

			send_contact(so, 0)
			so.db_set("b2c_payment_request_sent", 1, update_modified=False)
		return STATE_WAIT_PAYMENT

	if needs_multisizer(so):
		ensure_multisizer_po(so)
		set_state(so, STATE_WAIT_SIZE)
		return STATE_WAIT_SIZE

	if needs_address_check(so):
		ok, message = check_address(so.shipping_address_name)
		so.db_set("b2c_address_check", "OK" if ok else message, update_modified=False)
		if not ok:
			set_state(so, STATE_ADDRESS, message)
			return STATE_ADDRESS

	ensure_dropship_po(so)
	set_state(so, STATE_READY, trigger)
	return STATE_READY


@frappe.whitelist()
def evaluate_order(sales_order):
	"""Manual re-check from the form (button / server script)."""
	frappe.only_for(("System Manager", "Sales User", "Sales Manager"))
	return evaluate(sales_order, trigger="manuell")


def on_update_after_submit(doc, method=None):
	"""Manual workflow actions: back to "Offen" (payment received, size entered, address
	confirmed, resume) re-runs the gates; "Versendet" books the delivery and the invoice."""
	state = doc.get(STATE_FIELD)
	if state == STATE_OPEN:
		frappe.enqueue(
			"ecommerce_integrations.b2c.gates.evaluate",
			sales_order=doc.name,
			trigger="Workflow-Aktion",
			enqueue_after_commit=True,
		)
	elif state == STATE_SHIPPED and (flt(doc.per_delivered) < 100 or flt(doc.per_billed) < 100):
		frappe.enqueue(
			"ecommerce_integrations.b2c.gates.mark_shipped",
			sales_order=doc.name,
			enqueue_after_commit=True,
		)


def live_purchase_orders(so):
	return sorted(
		set(
			frappe.get_all(
				"Purchase Order Item",
				filters={"sales_order": so.name, "docstatus": 1},
				pluck="parent",
			)
		)
	)


def deliver_dropship(so):
	"""ERPNext's "Deliver (Dropship)" for every submitted purchase order behind the order: the
	PO becomes "Delivered" and the sales order's % Delivered follows (no Delivery Note in the
	dropship model)."""
	delivered = []
	for name in live_purchase_orders(so):
		po = frappe.get_doc("Purchase Order", name)
		changed = False
		# Mirrors PurchaseOrder.update_dropship_received_qty(): the delivered quantity of a
		# dropship line is its received_qty, and the sales order's % Delivered sums those.
		for item in po.items:
			if item.delivered_by_supplier and flt(item.received_qty) < flt(item.qty):
				item.db_set("received_qty", item.qty, update_modified=False)
				changed = True
		if not changed:
			continue
		po.update_receiving_percentage()
		po.set_status(update=True)
		po.update_delivered_qty_in_sales_order()
		delivered.append(name)
	return delivered


def ensure_sales_invoice(so):
	"""Invoice at shipping (decision 2026-09-03): one submitted Sales Invoice per order. The
	% Amount Billed of the sales order follows from it; payment is matched later
	(Shopify payout / bank feed) and closes the order."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	if flt(so.per_billed) >= 100:
		return None
	existing = frappe.db.get_value(
		"Sales Invoice Item", {"sales_order": so.name, "docstatus": 1}, "parent"
	)
	if existing:
		return existing
	si = make_sales_invoice(so.name, ignore_permissions=True)
	si.set_posting_time = 1
	si.posting_date = nowdate()
	si.due_date = nowdate()
	for field in ("shopify_order_id", "shopify_order_number"):
		if so.get(field) and si.meta.has_field(field):
			si.set(field, so.get(field))
	si.flags.ignore_mandatory = True
	si.insert(ignore_permissions=True)
	si.submit()
	return si.name


def mark_shipped(sales_order, tracking_number=None, carrier=None):
	"""Shipment signal - today the manual action "Versendet", later the feedback channel
	from Oro/Marello with the tracking number. Books what ERPNext needs to show the order as
	delivered and billed, then keeps the state."""
	so = frappe.get_doc("Sales Order", sales_order)
	if so.docstatus != 1:
		return
	if tracking_number:
		so.db_set(
			{"b2c_tracking_number": tracking_number, "b2c_carrier": carrier or ""}, update_modified=False
		)
	delivered = deliver_dropship(so)
	invoice = ensure_sales_invoice(so)
	so.reload()
	set_state(so, STATE_SHIPPED, tracking_number)
	log_gate(
		so,
		"Versand gebucht: "
		+ (f"PO geliefert {', '.join(delivered)}; " if delivered else "")
		+ (f"Rechnung {invoice}; " if invoice else "")
		+ f"geliefert {flt(so.per_delivered):.0f} %, berechnet {flt(so.per_billed):.0f} %",
	)
	return {"delivered": delivered, "invoice": invoice, "per_delivered": so.per_delivered, "per_billed": so.per_billed}


def mark_paid(sales_order, financial_status="paid"):
	"""Payment signal (Shopify orders/paid webhook or bank feed): store it and re-run the gates."""
	frappe.db.set_value(
		"Sales Order", sales_order, "shopify_financial_status", financial_status, update_modified=False
	)
	return evaluate(sales_order, trigger=f"Zahlung: {financial_status}")
