"""Returns and refunds (B2C). Shopify's `refunds/create` webhook is the source: a refund after
shipping becomes a credit note (Sales Invoice return) against the order's invoice and moves the
order to "Retoure"; a refund before shipping only records the money and parks the order when
it was refunded in full. The replacement path (new purchase order at Adomio) is a manual
workflow action, see gates.ensure_replacement_po().
"""

import json

import frappe
from frappe.utils import cint, flt

from ecommerce_integrations.b2c import gates
from ecommerce_integrations.b2c.reminders import money
from ecommerce_integrations.shopify.constants import ORDER_ID_FIELD
from ecommerce_integrations.shopify.utils import create_shopify_log

REFUNDED_AMOUNT_FIELD = "b2c_refunded_amount"


def refund_amount(refund):
	"""Money that went back to the customer: successful refund transactions, else the line subtotals."""
	total = 0.0
	for transaction in refund.get("transactions") or []:
		if (transaction.get("kind") or "").lower() != "refund":
			continue
		if (transaction.get("status") or "success").lower() not in ("success", "pending"):
			continue
		total += flt(transaction.get("amount"))
	if total:
		return total
	for line in refund.get("refund_line_items") or []:
		total += flt(line.get("subtotal")) + flt(line.get("total_tax"))
	return total


def refunded_quantities(refund):
	"""Refunded quantity per shop line: (sku, variant_id, product_id) -> qty."""
	result = {}
	for line in refund.get("refund_line_items") or []:
		item = line.get("line_item") or {}
		qty = cint(line.get("quantity"))
		if qty <= 0:
			continue
		key = (item.get("sku"), item.get("variant_id"), item.get("product_id"))
		result[key] = result.get(key, 0) + qty
	return result


def match_rows(rows, refund):
	"""Map the refunded shop lines onto document rows by item code (first match wins per row).
	Returns [(row, qty)]; lines the document does not carry are ignored."""
	from ecommerce_integrations.shopify.product import get_item_code

	wanted = {}
	for (sku, variant_id, product_id), qty in refunded_quantities(refund).items():
		# The B2C item code is the shop SKU (import rule); the Ecommerce Item lookup covers
		# variants whose SKU differs from the item code.
		item_code = get_item_code({"sku": sku, "variant_id": variant_id, "product_id": product_id}) or sku
		if item_code:
			wanted[item_code] = wanted.get(item_code, 0) + qty
	matched = []
	for row in rows:
		left = wanted.get(row.item_code, 0)
		if left <= 0:
			continue
		# Rows of a return document already carry negative quantities.
		qty = min(left, abs(cint(row.qty)))
		if qty <= 0:
			continue
		matched.append((row, qty))
		wanted[row.item_code] = left - qty
	return matched


def submitted_invoice(so):
	return frappe.db.get_value("Sales Invoice Item", {"sales_order": so.name, "docstatus": 1}, "parent")


def make_credit_note(so, refund):
	"""Sales Invoice return against the order's invoice for the refunded lines (all lines when
	the refund names none, e.g. a plain amount refund). Returns the credit note name."""
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	invoice = submitted_invoice(so)
	if not invoice:
		return None
	credit = make_return_doc("Sales Invoice", invoice)
	matched = match_rows(credit.items, refund)
	if matched:
		# The mapped rows are unsaved (no name yet): key them by object identity.
		keep = {id(row): qty for row, qty in matched}
		credit.items = [row for row in credit.items if id(row) in keep]
		for row in credit.items:
			row.qty = -abs(keep[id(row)])
			row.stock_qty = row.qty * flt(row.conversion_factor or 1)
	credit.set_posting_time = 1
	credit.posting_date = frappe.utils.nowdate()
	credit.due_date = credit.posting_date
	credit.remarks = f"Shopify-Erstattung {refund.get('id')}: {refund.get('note') or ''}".strip()
	credit.flags.ignore_mandatory = True
	credit.insert(ignore_permissions=True)
	credit.submit()
	return credit.name


def apply_refund(so, refund):
	"""Book a Shopify refund on the sales order. Returns what happened for the log."""
	amount = refund_amount(refund)
	refunded = flt(so.get(REFUNDED_AMOUNT_FIELD)) + amount
	so.db_set(REFUNDED_AMOUNT_FIELD, refunded, update_modified=False)
	full = refunded >= flt(so.grand_total) - 0.005
	state = so.get(gates.STATE_FIELD)

	if submitted_invoice(so):
		credit = make_credit_note(so, refund)
		so.reload()
		if state not in (gates.STATE_RETURN, gates.STATE_COMPLETED, gates.STATE_CANCELLED):
			gates.set_state(so, gates.STATE_RETURN, f"Erstattung {money(amount, so.currency)}")
		gates.log_gate(
			so,
			f"Shopify-Erstattung {refund.get('id')}: {money(amount, so.currency)}"
			+ (f", Gutschrift {credit}" if credit else "")
			+ (" (vollständig erstattet)" if full else ""),
		)
		return {"credit_note": credit, "amount": amount, "full": full}

	# Before shipping: nothing is invoiced yet. A full refund means the order must not be
	# produced any more - park it; the cancellation itself comes through orders/cancelled.
	so.db_set("shopify_financial_status", "refunded" if full else "partially_refunded", update_modified=False)
	if full and state not in (gates.STATE_ON_HOLD, gates.STATE_CANCELLED, gates.STATE_ARCHIVED):
		gates.set_state(so, gates.STATE_ON_HOLD, "vollständig erstattet vor Versand")
	gates.log_gate(
		so,
		f"Shopify-Erstattung {refund.get('id')} vor Versand: {money(amount, so.currency)}"
		+ (" (vollständig erstattet, Auftrag angehalten)" if full else ""),
	)
	return {"credit_note": None, "amount": amount, "full": full}


def prepare_credit_note(payload, request_id=None, shopify_account=None):
	"""Called by the refunds/create webhook."""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	refund = json.loads(payload) if isinstance(payload, str) else payload
	if isinstance(shopify_account, str):
		shopify_account = frappe.get_doc("Shopify Account", shopify_account)
	account_name = shopify_account.name if shopify_account else None

	try:
		order_id = refund.get("order_id")
		sales_order = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: order_id, "docstatus": 1})
		if not sales_order:
			create_shopify_log(status="Invalid", message="Sales Order not found for refund", shopify_account=account_name)
			return
		so = frappe.get_doc("Sales Order", sales_order)
		if frappe.db.exists("Comment", {"reference_doctype": "Sales Order", "reference_name": so.name, "content": ["like", f"%Shopify-Erstattung {refund.get('id')}%"]}):
			create_shopify_log(status="Success", message="Refund already booked", shopify_account=account_name)
			return
		result = apply_refund(so, refund)
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True, shopify_account=account_name)
	else:
		create_shopify_log(status="Success", message=json.dumps(result, default=str), shopify_account=account_name)
