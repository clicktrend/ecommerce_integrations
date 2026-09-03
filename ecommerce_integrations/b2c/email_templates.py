"""E-mail templates of the B2C order workflow - Marello's customer mails (oro_email_template:
order_incomplete, order_payment_reminder, order_payment_reminder_2nd, order_invoiced,
order_multisizer, order_complete, order_cancelled), converted from Twig over the Marello order
to Jinja over the Sales Order (`doc`) plus a prepared context (`b2c`, see reminders.template_context).

    bench --site <site> execute ecommerce_integrations.b2c.email_templates.install
"""

import os

import frappe

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# Email Template name -> (file, subject)
TEMPLATES = {
	"B2C Zahlungsaufforderung": (
		"zahlungsaufforderung.html",
		"[Wichtig] Ihre Bestellung kann noch nicht bearbeitet werden. Bitte beachten Sie folgende Punkte! [OR#{{ b2c.order_number }}]",
	),
	"B2C Zahlungserinnerung 1": (
		"zahlungserinnerung_1.html",
		"[Wichtig] Ihre Bestellung ist weiterhin unvollständig! #{{ b2c.order_number }}",
	),
	"B2C Zahlungserinnerung 2": (
		"zahlungserinnerung_2.html",
		"[Wichtig] Letzte Zahlungserinnerung #{{ b2c.order_number }}",
	),
	"B2C Versandbestätigung": (
		"versandbestaetigung.html",
		"Ihre Bestellung wurde versendet. [OR#{{ b2c.order_number }}]",
	),
	"B2C Multisizer versendet": (
		"multisizer_versendet.html",
		"Ihr Ringmaß (Multisizer) wurde versendet. [OR#{{ b2c.order_number }}]",
	),
	"B2C Bestellbestätigung": (
		"bestellbestaetigung.html",
		"Danke! Ihre Bestellung wird vorbereitet. [OR#{{ b2c.order_number }}]",
	),
	"B2C Stornierung": (
		"stornierung.html",
		"Ihre Bestellung #{{ b2c.order_number }} wurde storniert!",
	),
}


def install():
	created, updated = [], []
	for name, (filename, subject) in TEMPLATES.items():
		with open(os.path.join(HERE, filename), encoding="utf-8") as f:
			html = f.read()
		values = {"subject": subject, "use_html": 1, "response_html": html, "response": ""}
		if frappe.db.exists("Email Template", name):
			doc = frappe.get_doc("Email Template", name)
			doc.update(values)
			doc.save(ignore_permissions=True)
			updated.append(name)
		else:
			frappe.get_doc({"doctype": "Email Template", "__newname": name, **values}).insert(
				ignore_permissions=True
			)
			created.append(name)
	frappe.db.commit()
	return f"created {len(created)}, updated {len(updated)}: {', '.join(TEMPLATES)}"
