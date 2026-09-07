"""Booking the money that is already there (accounting doc §8b no. 6).

A marketplace or shop order arrives paid: the buyer settled with Amazon, Shopify Payments or PayPal
long before we invoice. sevdesk books that payment itself (SevdeskCommand::handleDone books the full
amount against a check account); ERPNext does not, so every invoice would stand open, dunning would
start, and the print format would ask the buyer to transfer the money a second time.

The counterpart is NOT a bank account but the gateway's clearing account (accounting doc rule 2.1
no. 3): B2C books invoice -> clearing, and B2B books the payout bank <- clearing, so the balance
closes where the two instances meet. The account hangs on the Mode of Payment, which is where the
tax advisor's numbers were seeded (Amazon 1361, PayPal 1212, Vorkasse 1207).
"""

import frappe
from frappe.utils import flt

from ecommerce_integrations.b2c.gates import PAID_MARKERS, PAYMENT_GATEWAY_FIELD, PAYMENT_STATUS_FIELD, log_gate

# What the connectors write into b2c_payment_gateway -> the Mode of Payment carrying the account.
GATEWAY_MODES = {
	"amazon": "Amazon",
	"paypal": "PayPal Mit Gravur",
	"shopify_payments": "Shopify Payments",
	"bank deposit": "Vorkasse",
	"vorkasse": "Vorkasse",
	"manual": "Vorkasse",
}


def mode_for(gateway):
	"""Pure mapping, so the table above can be tested without a database."""
	return GATEWAY_MODES.get((gateway or "").strip().lower())


def is_paid(so):
	return (so.get(PAYMENT_STATUS_FIELD) or "") in PAID_MARKERS


def clearing_account(mode_of_payment, company):
	return frappe.db.get_value(
		"Mode of Payment Account", {"parent": mode_of_payment, "company": company}, "default_account"
	)


def ensure_payment_entry(invoice_name, so):
	"""Book the payment of an already settled order against the gateway's clearing account.
	Returns the payment entry, or None with a line in the order's gate log saying why not."""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	if not is_paid(so):
		return None
	invoice = frappe.get_doc("Sales Invoice", invoice_name)
	if invoice.docstatus != 1 or flt(invoice.outstanding_amount) <= 0:
		return None

	gateway = so.get(PAYMENT_GATEWAY_FIELD)
	mode = mode_for(gateway)
	if not mode:
		log_gate(so, f"Zahlung nicht gebucht: unbekannter Zahlungsweg „{gateway or '-'}“")
		return None
	account = clearing_account(mode, invoice.company)
	if not account:
		# Shopify Payments today: the tax advisor named 11902, but that is a DATEV personal account,
		# not a ledger account - the clearing account for it is still open (accounting doc §5a).
		log_gate(so, f"Zahlung nicht gebucht: „{mode}“ hat kein Konto hinterlegt")
		return None

	entry = get_payment_entry("Sales Invoice", invoice.name)
	entry.mode_of_payment = mode
	entry.paid_to = account
	entry.reference_no = so.get("po_no") or invoice.po_no or so.name
	entry.reference_date = invoice.posting_date
	entry.flags.ignore_permissions = True
	entry.insert()
	entry.submit()
	log_gate(so, f"Zahlung gebucht: {entry.name} über {mode} auf {account}")
	return entry.name
