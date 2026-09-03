"""Idempotent installer for the B2C order workflow: custom fields, the multisizer item, the
Workflow States and Actions, and the Workflow "B2C Auftrag" on Sales Order.

    bench --site <site> execute ecommerce_integrations.b2c.workflow_setup.install
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ecommerce_integrations.b2c import gates as g

ROLE = "Sales User"

# state, docstatus, style
STATES = [
	(g.STATE_DRAFT, "0", ""),
	(g.STATE_OPEN, "1", "Primary"),
	(g.STATE_WAIT_PAYMENT, "1", "Warning"),
	(g.STATE_WAIT_SIZE, "1", "Warning"),
	(g.STATE_ADDRESS, "1", "Warning"),
	(g.STATE_READY, "1", "Info"),
	(g.STATE_IN_PRODUCTION, "1", "Info"),
	(g.STATE_SHIPPED, "1", "Success"),
	(g.STATE_COMPLETED, "1", "Success"),
	(g.STATE_ON_HOLD, "1", "Danger"),
	(g.STATE_WAIT_FEEDBACK, "1", "Warning"),
	(g.STATE_ARCHIVED, "1", "Inverse"),
	(g.STATE_RETURN, "1", "Danger"),
	(g.STATE_CANCELLED, "2", "Danger"),
]

# from, action, to  (manual actions; automation writes the state field directly)
TRANSITIONS = [
	(g.STATE_WAIT_PAYMENT, "Zahlung eingegangen", g.STATE_OPEN),
	(g.STATE_WAIT_SIZE, "Ringgröße eingetragen", g.STATE_OPEN),
	(g.STATE_ADDRESS, "Adresse bestätigt", g.STATE_OPEN),
	(g.STATE_ADDRESS, "Adresse korrigiert", g.STATE_OPEN),
	(g.STATE_OPEN, "Anhalten", g.STATE_ON_HOLD),
	(g.STATE_WAIT_PAYMENT, "Anhalten", g.STATE_ON_HOLD),
	(g.STATE_WAIT_SIZE, "Anhalten", g.STATE_ON_HOLD),
	(g.STATE_ADDRESS, "Anhalten", g.STATE_ON_HOLD),
	(g.STATE_READY, "Anhalten", g.STATE_ON_HOLD),
	(g.STATE_ON_HOLD, "Fortsetzen", g.STATE_OPEN),
	(g.STATE_OPEN, "Rückfrage stellen", g.STATE_WAIT_FEEDBACK),
	(g.STATE_WAIT_PAYMENT, "Rückfrage stellen", g.STATE_WAIT_FEEDBACK),
	(g.STATE_WAIT_SIZE, "Rückfrage stellen", g.STATE_WAIT_FEEDBACK),
	(g.STATE_ADDRESS, "Rückfrage stellen", g.STATE_WAIT_FEEDBACK),
	(g.STATE_WAIT_FEEDBACK, "Fortsetzen", g.STATE_OPEN),
	(g.STATE_WAIT_PAYMENT, "Archivieren", g.STATE_ARCHIVED),
	(g.STATE_WAIT_FEEDBACK, "Archivieren", g.STATE_ARCHIVED),
	(g.STATE_WAIT_SIZE, "Archivieren", g.STATE_ARCHIVED),
	(g.STATE_READY, "In Produktion", g.STATE_IN_PRODUCTION),
	(g.STATE_IN_PRODUCTION, "Versendet", g.STATE_SHIPPED),
	(g.STATE_SHIPPED, "Abschließen", g.STATE_COMPLETED),
	(g.STATE_SHIPPED, "Retoure", g.STATE_RETURN),
	(g.STATE_RETURN, "Abschließen", g.STATE_COMPLETED),
	# Replacement after a return: a fresh purchase order at Adomio for the same lines.
	(g.STATE_RETURN, "Ersatz fertigen", g.STATE_READY),
	# Adomio dropped the order (feedback "cancelled") or a person stops it while it is produced.
	(g.STATE_IN_PRODUCTION, "Anhalten", g.STATE_ON_HOLD),
]

CUSTOM_FIELDS = {
	"Sales Order": [
		{
			"fieldname": "b2c_workflow_section",
			"label": "B2C-Workflow",
			"fieldtype": "Section Break",
			"insert_after": "shopify_ordered_at",
			"collapsible": 1,
		},
		{
			"fieldname": "b2c_payment_request_sent",
			"label": "Zahlungsaufforderung gesendet",
			"fieldtype": "Check",
			"insert_after": "b2c_workflow_section",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_reminder_count",
			"label": "Kontakte (Zahlung)",
			"fieldtype": "Int",
			"insert_after": "b2c_payment_request_sent",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_last_reminder_on",
			"label": "Letzter Kontakt am",
			"fieldtype": "Date",
			"insert_after": "b2c_reminder_count",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_workflow_column",
			"fieldtype": "Column Break",
			"insert_after": "b2c_last_reminder_on",
		},
		{
			"fieldname": "b2c_address_check",
			"label": "Adressprüfung",
			"fieldtype": "Data",
			"insert_after": "b2c_workflow_column",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_address_confirmed",
			"label": "Adresse manuell bestätigt",
			"fieldtype": "Check",
			"insert_after": "b2c_address_check",
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_tracking_number",
			"label": "Sendungsnummer",
			"fieldtype": "Data",
			"insert_after": "b2c_address_confirmed",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_carrier",
			"label": "Versanddienstleister",
			"fieldtype": "Data",
			"insert_after": "b2c_tracking_number",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_shipping_mail_sent",
			"label": "Versandmail gesendet",
			"fieldtype": "Check",
			"insert_after": "b2c_carrier",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_shopify_fulfillment_id",
			"label": "Shopify-Fulfillment",
			"fieldtype": "Data",
			"insert_after": "b2c_carrier",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_refunded_amount",
			"label": "Erstattet",
			"fieldtype": "Currency",
			"options": "currency",
			"insert_after": "b2c_shopify_fulfillment_id",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_gauge_mail_sent",
			"label": "Multisizer-Mail gesendet",
			"fieldtype": "Check",
			"insert_after": "b2c_shipping_mail_sent",
			"read_only": 1,
			"allow_on_submit": 1,
		},
	],
	"Purchase Order": [
		{
			"fieldname": "b2c_replacement_of",
			"label": "Ersatz für Bestellung",
			"fieldtype": "Link",
			"options": "Purchase Order",
			"insert_after": "supplier_name",
			"read_only": 1,
			"description": "Ersatzfertigung nach Retoure: die ursprüngliche Bestellung an Adomio.",
		},
	],
}


def ensure_states():
	for state, _docstatus, style in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state, "style": style}).insert(
				ignore_permissions=True
			)


def ensure_actions():
	for _from, action, _to in TRANSITIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def ensure_multisizer_item():
	if frappe.db.exists("Item", g.MULTISIZER_ITEM):
		return
	group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": g.MULTISIZER_ITEM,
			"item_name": "Multisizer (Ringmaß)",
			"item_group": group,
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"delivered_by_supplier": 1,
			"description": "Ringmaß, das der Kunde vorab per Brief erhält, um seine Ringgröße zu bestimmen.",
		}
	).insert(ignore_permissions=True)


def ensure_workflow():
	values = {
		"document_type": "Sales Order",
		"is_active": 1,
		"override_status": 0,
		"send_email_alert": 0,
		"workflow_state_field": g.STATE_FIELD,
		"states": [
			{"state": state, "doc_status": docstatus, "allow_edit": ROLE, "update_field": "", "update_value": ""}
			for state, docstatus, _style in STATES
		],
		"transitions": [
			{"state": src, "action": action, "next_state": dst, "allowed": ROLE, "allow_self_approval": 1}
			for src, action, dst in TRANSITIONS
		],
	}
	if frappe.db.exists("Workflow", g.WORKFLOW_NAME):
		doc = frappe.get_doc("Workflow", g.WORKFLOW_NAME)
		doc.set("states", [])
		doc.set("transitions", [])
		doc.update(values)
		doc.save(ignore_permissions=True)
		return "updated"
	frappe.get_doc({"doctype": "Workflow", "workflow_name": g.WORKFLOW_NAME, **values}).insert(
		ignore_permissions=True
	)
	return "created"


def install():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	ensure_multisizer_item()
	ensure_states()
	ensure_actions()
	result = ensure_workflow()
	frappe.db.commit()
	return f"Workflow '{g.WORKFLOW_NAME}' {result}; {len(STATES)} states, {len(TRANSITIONS)} transitions"
