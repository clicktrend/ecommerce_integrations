"""Payment reminders for orders waiting for money - the staff handbook's cadence
(BookStack page 108 "Ordnungssystem bei fortlaufenden Bestellungen"): first contact at once,
a reminder after 14 days, three contacts in total, then the order is archived by a human.
There is no automatic cancellation.
"""

import frappe
from frappe.utils import add_days, date_diff, getdate, nowdate

from ecommerce_integrations.b2c.gates import STATE_FIELD, STATE_WAIT_PAYMENT, log_gate

REMINDER_INTERVAL_DAYS = 14
MAX_CONTACTS = 3  # payment request + reminder I + reminder II

TEMPLATES = {
	0: "B2C Zahlungsaufforderung",
	1: "B2C Zahlungserinnerung 1",
	2: "B2C Zahlungserinnerung 2",
}


def recipient_for(so):
	"""The Shopify import keeps the buyer's e-mail on the Address documents (billing, then
	shipping), not on the Customer - so look there before falling back to the customer."""
	if so.get("contact_email"):
		return so.contact_email
	for address_name in (so.get("customer_address"), so.get("shipping_address_name")):
		email = address_name and frappe.db.get_value("Address", address_name, "email_id")
		if email:
			return email
	return frappe.db.get_value("Customer", so.customer, "email_id")


def money(value, currency):
	return frappe.format(value, {"fieldtype": "Currency"}, currency=currency)


def address_html(address_name):
	if not address_name:
		return ""
	a = frappe.db.get_value(
		"Address", address_name, ["address_line1", "address_line2", "pincode", "city", "country"], as_dict=True
	)
	if not a:
		return ""
	lines = [a.address_line1, a.address_line2, f"{a.pincode or ''} {a.city or ''}".strip(), a.country]
	return "<br>".join(frappe.utils.escape_html(line) for line in lines if line)


def company_context(company):
	c = frappe.db.get_value(
		"Company", company, ["company_name", "tax_id", "website", "email", "fax", "company_logo"], as_dict=True
	) or frappe._dict()
	address = frappe.db.get_value(
		"Address",
		{"is_your_company_address": 1, "address_title": company},
		["address_line1", "pincode", "city"],
		as_dict=True,
	) or frappe._dict()
	bank = frappe.db.get_value(
		"Bank Account", {"company": company, "is_company_account": 1, "is_default": 1}, ["bank", "iban", "account_name"], as_dict=True
	) or frappe._dict()
	return {
		"company_name": c.company_name or company,
		"company_address_line": ", ".join(filter(None, [address.address_line1, f"{address.pincode or ''} {address.city or ''}".strip()])),
		"company_fax_line": f"Fax: {c.fax}" if c.fax else "",
		"company_email_line": f"E-Mail: {c.email}" if c.email else "",
		"company_website_line": f"Webseite: {c.website}" if c.website else "",
		"company_tax_line": f"Umsatzsteuer-Identifikationsnummer gemäß § 27a Umsatzsteuergesetz: {c.tax_id}" if c.tax_id else "",
		"logo_url": frappe.utils.get_url(c.company_logo) if c.company_logo else "",
		"bank_owner": bank.account_name or c.company_name or company,
		"bank_iban": bank.iban or "",
		"bank_name": bank.bank or "",
	}


def template_context(so):
	"""`doc` = the sales order, `b2c` = everything Marello's templates took from the order
	entity, the channel and the workflow data - pre-formatted here so the templates stay plain."""
	from ecommerce_integrations.b2c.gates import STATE_FIELD, STATE_WAIT_PAYMENT, STATE_WAIT_SIZE

	currency = so.currency
	items = [
		{
			"name": row.item_name or row.item_code,
			"sku": row.item_code,
			"qty": int(row.qty) if float(row.qty).is_integer() else row.qty,
			"price": money(row.rate, currency),
			"tax": "",
			"total": money(row.amount, currency),
		}
		for row in so.items
	]
	ctx = {
		"order_number": (so.get("shopify_order_number") or so.name).lstrip("#"),
		"ordered_at": frappe.utils.format_datetime(so.get("shopify_ordered_at") or so.creation, "dd.MM.yyyy HH:mm"),
		"grand_total": money(so.grand_total, currency),
		"net_total": money(so.net_total, currency),
		"total_taxes": money(so.total_taxes_and_charges, currency),
		"shipping": money(so.total_taxes_and_charges, currency),
		"billing_address": address_html(so.customer_address),
		"shipping_address": address_html(so.shipping_address_name),
		"items": items,
		"shop_name": frappe.db.get_value("Company", so.company, "company_name") or so.company,
		"carrier": (so.get("b2c_carrier") or "").upper(),
		"unpaid": so.get(STATE_FIELD) == STATE_WAIT_PAYMENT,
		"size_missing": so.get(STATE_FIELD) == STATE_WAIT_SIZE,
	}
	ctx.update(company_context(so.company))
	return {"doc": so.as_dict(), "b2c": ctx}


def render(so, template_name):
	"""(subject, html) for a B2C Email Template - or None when the template is not installed."""
	if not frappe.db.exists("Email Template", template_name):
		return None
	tpl = frappe.get_doc("Email Template", template_name)
	ctx = template_context(so)
	subject = frappe.render_template(tpl.subject, ctx)
	body = frappe.render_template(tpl.response_html if tpl.use_html else tpl.response, ctx)
	return subject, body


def send_template(so, template_name, attachments=None, fallback_subject=None, fallback_body=None):
	"""Send a B2C mail to the buyer; without the template the fallback text goes out."""
	recipient = recipient_for(so)
	rendered = render(so, template_name)
	subject, body = rendered if rendered else (fallback_subject, fallback_body)
	if not recipient or not body:
		log_gate(so, f"Mail „{template_name}“ nicht gesendet: {'keine Empfängeradresse' if not recipient else 'kein Template und kein Standardtext'}")
		return False
	try:
		frappe.sendmail(
			recipients=[recipient],
			subject=subject,
			message=body,
			attachments=attachments or [],
			reference_doctype="Sales Order",
			reference_name=so.name,
			delayed=True,
		)
	except Exception as exc:  # a mail must never roll back the order bookkeeping
		frappe.log_error(title=f"B2C mail {template_name} for {so.name}", message=frappe.get_traceback())
		log_gate(so, f"Mail „{template_name}“ NICHT gesendet: {exc}")
		return False
	log_gate(so, f"Mail „{template_name}“ an {recipient}")
	return True


def send_contact(so, contact_no):
	"""Send the n-th contact (0 = payment request, then the reminders) and record it."""
	template = TEMPLATES.get(contact_no)
	fallback = (
		f"Guten Tag,<br><br>für Ihre Bestellung {so.get('shopify_order_number') or so.name} über "
		f"{money(so.grand_total, so.currency)} liegt uns noch kein Zahlungseingang vor. Bitte überweisen "
		"Sie den Betrag, damit wir mit der Gravur beginnen können."
	)
	send_template(so, template, fallback_subject=f"Zahlung zu Bestellung {so.get('shopify_order_number') or so.name}", fallback_body=fallback)
	so.db_set(
		{"b2c_reminder_count": contact_no + 1, "b2c_last_reminder_on": nowdate()},
		update_modified=False,
	)


def send_due_reminders():
	"""Daily: every order waiting for payment whose last contact is 14 days old gets the next one."""
	waiting = frappe.get_all(
		"Sales Order",
		filters={"docstatus": 1, STATE_FIELD: STATE_WAIT_PAYMENT},
		fields=["name", "b2c_reminder_count", "b2c_last_reminder_on", "transaction_date"],
	)
	for row in waiting:
		count = row.b2c_reminder_count or 0
		if count >= MAX_CONTACTS:
			continue
		last = row.b2c_last_reminder_on or row.transaction_date
		if date_diff(nowdate(), getdate(last)) < REMINDER_INTERVAL_DAYS:
			continue
		so = frappe.get_doc("Sales Order", row.name)
		send_contact(so, count)
		frappe.db.commit()


def open_older_than(days=3):
	"""The handbook's check list: Shopify orders still open after three days (page 95)."""
	cutoff = add_days(nowdate(), -days)
	return frappe.get_all(
		"Sales Order",
		filters={
			"docstatus": 1,
			"shopify_account": ["is", "set"],
			"transaction_date": ["<=", cutoff],
			STATE_FIELD: ["not in", ["Versendet", "Abgeschlossen", "Storniert", "Archiviert", "In Produktion", "Produktionsbereit"]],
		},
		fields=["name", "shopify_order_number", STATE_FIELD, "transaction_date"],
		order_by="transaction_date asc",
	)
