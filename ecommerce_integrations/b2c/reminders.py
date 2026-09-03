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


def send_contact(so, contact_no):
	"""Send the n-th contact (0 = payment request). Uses the Email Template of that name when it
	exists, otherwise a plain text - and always records the contact on the order."""
	recipient = recipient_for(so)
	template = TEMPLATES.get(contact_no)
	subject = f"{template or 'Zahlung'} zu Bestellung {so.shopify_order_number or so.name}"
	message = None
	if template and frappe.db.exists("Email Template", template):
		rendered = frappe.get_doc("Email Template", template).get_formatted_email(so.as_dict())
		subject, message = rendered.get("subject") or subject, rendered.get("message")
	if not message:
		message = (
			f"Guten Tag,<br><br>für Ihre Bestellung {so.shopify_order_number or so.name} "
			f"über {frappe.format(so.grand_total, {'fieldtype': 'Currency'})} liegt uns noch kein "
			"Zahlungseingang vor. Bitte überweisen Sie den Betrag, damit wir mit der Gravur beginnen können."
		)
	if recipient:
		frappe.sendmail(
			recipients=[recipient],
			subject=subject,
			message=message,
			reference_doctype="Sales Order",
			reference_name=so.name,
			delayed=False,
		)
	so.db_set(
		{"b2c_reminder_count": contact_no + 1, "b2c_last_reminder_on": nowdate()},
		update_modified=False,
	)
	log_gate(so, f"Kontakt {contact_no + 1}/{MAX_CONTACTS} ({template}) an {recipient or 'keine Adresse'}")


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
