"""Idempotent installer for the B2C order workflow: custom fields, the multisizer item, the
Workflow States and Actions, and the Workflow "B2C Auftrag" on Sales Order.

    bench --site <site> execute ecommerce_integrations.b2c.workflow_setup.install
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from ecommerce_integrations.b2c import channel
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

# from, action, to [, condition]  (manual actions; automation writes the state field directly)
TRANSITIONS = [
	(g.STATE_WAIT_PAYMENT, "Zahlung eingegangen", g.STATE_OPEN),
	(g.STATE_WAIT_SIZE, "Ringgröße eingetragen", g.STATE_OPEN),
	# "bestätigt" only once the flag is set (dialog "Adresse prüfen" or the checkbox) - without
	# it the gates would send the order straight back to "Adressprüfung".
	(g.STATE_ADDRESS, "Adresse bestätigt", g.STATE_OPEN, "doc.b2c_address_confirmed"),
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

# Fields renamed on 2026-09-08 (old -> new): copied once by migrate_legacy_fields(), then the old
# Custom Field is dropped (the column stays; frappe never drops columns).
LEGACY_FIELDS = {
	"b2c_payment_status": "integration_payment_status",
	"b2c_payment_gateway": "integration_payment_gateway",
}

CUSTOM_FIELDS = {
	"Sales Order": [
		# --- the channel contract (b2c.channel.CONTRACT_FIELDS): what every integration writes ---
		{
			"fieldname": "integration_section",
			"label": "Sales Channel",
			"fieldtype": "Section Break",
			"insert_after": "po_date",
			"depends_on": "eval:doc.sales_channel",
		},
		{
			# One filter for every integration (user decision 2026-09-08): the channel, not the
			# connector account, tells the orders apart in the list.
			"fieldname": "sales_channel",
			"label": "Sales Channel",
			"fieldtype": "Link",
			"options": "Sales Channel",
			"insert_after": "integration_section",
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "integration_order_id",
			"label": "Bestellnummer (Kanal)",
			"fieldtype": "Data",
			"insert_after": "sales_channel",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
		},
		{
			"fieldname": "integration_ordered_at",
			"label": "Bestellt am (Kanal)",
			"fieldtype": "Datetime",
			"insert_after": "integration_order_id",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "integration_column",
			"fieldtype": "Column Break",
			"insert_after": "integration_ordered_at",
		},
		{
			# Generic priority (user decisions 2026-06-25 and 2026-09-08): Amazon Prime is "High",
			# a shop's express option or a person may set it too - so it is editable after submit.
			"fieldname": "integration_priority",
			"label": "Prio",
			"fieldtype": "Select",
			"options": "\nNormal\nHigh",
			"default": "Normal",
			"insert_after": "integration_column",
			"allow_on_submit": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "integration_ship_by",
			"label": "Spätester Versand",
			"fieldtype": "Datetime",
			"insert_after": "integration_priority",
			"allow_on_submit": 1,
			"no_copy": 1,
			"in_list_view": 1,
		},
		{
			# Channel neutral payment marker (user decision 2026-09-04, variant B): every channel
			# writes it - Shopify from financial_status, Amazon as paid on import - and the payment
			# gate reads it. The gateway becomes the Mode of Payment on the invoice later.
			"fieldname": "integration_payment_status",
			"label": "Zahlstatus",
			"fieldtype": "Select",
			"options": "\nOffen\nBezahlt\nTeilweise erstattet\nErstattet",
			"insert_after": "integration_ship_by",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "integration_payment_gateway",
			"label": "Zahlungsweg",
			"fieldtype": "Data",
			"insert_after": "integration_payment_status",
			"read_only": 1,
			"allow_on_submit": 1,
			"no_copy": 1,
		},
		# --- the workflow's own bookkeeping ---
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
		{
			# Local copies of the photos / renders (b2c.personalization_files): K0 raw store with
			# a deletion clock - 30 days after shipment, 90 without one.
			"fieldname": "b2c_perso_files",
			"label": "Personalisierungsdateien",
			"fieldtype": "Select",
			"options": "\nAusstehend\nGeholt\nFehlgeschlagen\nGelöscht",
			"insert_after": "b2c_gauge_mail_sent",
			"read_only": 1,
			"allow_on_submit": 1,
		},
		{
			"fieldname": "b2c_perso_purge_after",
			"label": "Dateien löschen ab",
			"fieldtype": "Date",
			"insert_after": "b2c_perso_files",
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


def transition_rows():
	for src, action, dst, *rest in TRANSITIONS:
		yield src, action, dst, (rest[0] if rest else "")


def ensure_actions():
	for _from, action, _to, _condition in transition_rows():
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
			{
				"state": src,
				"action": action,
				"next_state": dst,
				"allowed": ROLE,
				"allow_self_approval": 1,
				"condition": condition,
			}
			for src, action, dst, condition in transition_rows()
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


def migrate_legacy_fields():
	"""Copy the renamed fields once (LEGACY_FIELDS), then drop the old Custom Field definitions."""
	copied = {}
	for old, new in LEGACY_FIELDS.items():
		if not frappe.db.exists("Custom Field", f"Sales Order-{old}"):
			continue
		if frappe.db.has_column("Sales Order", old) and frappe.db.has_column("Sales Order", new):
			pending = f"ifnull(`{new}`, '') = '' and ifnull(`{old}`, '') <> ''"
			copied[old] = frappe.db.sql(f"select count(*) from `tabSales Order` where {pending}")[0][0]
			frappe.db.sql(f"update `tabSales Order` set `{new}` = `{old}` where {pending}")
		frappe.delete_doc("Custom Field", f"Sales Order-{old}", ignore_permissions=True, force=True)
	if copied:
		frappe.clear_cache(doctype="Sales Order")
	return copied


def backfill_payment_status():
	"""Shopify orders created before the marker existed: derive it from the financial status."""
	rows = frappe.get_all(
		"Sales Order",
		filters={"shopify_financial_status": ["is", "set"], g.PAYMENT_STATUS_FIELD: ["in", ["", None]]},
		fields=["name", "shopify_financial_status", "shopify_payment_gateway"],
	)
	for row in rows:
		frappe.db.set_value(
			"Sales Order",
			row.name,
			{
				g.PAYMENT_STATUS_FIELD: g.payment_status_from_financial(row.shopify_financial_status),
				g.PAYMENT_GATEWAY_FIELD: row.shopify_payment_gateway or "",
			},
			update_modified=False,
		)
	return len(rows)


def backfill_shopify_orders():
	"""Shopify orders from before the channel contract: channel, shop number and order time from the
	Shopify fields. (The Amazon app backfills its own orders in its after_migrate.)"""
	rows = frappe.get_all(
		"Sales Order",
		filters={"shopify_account": ["is", "set"], channel.ORDER_FIELD: ["in", ["", None]]},
		fields=["name", "shopify_account", "shopify_order_number", "shopify_ordered_at", "integration_priority"],
	)
	for row in rows:
		frappe.db.set_value(
			"Sales Order",
			row.name,
			{
				channel.ORDER_FIELD: channel.ensure_for_integration("Shopify Account", row.shopify_account),
				"integration_order_id": row.shopify_order_number,
				"integration_ordered_at": row.shopify_ordered_at,
				"integration_priority": row.integration_priority or channel.PRIORITY_NORMAL,
			},
			update_modified=False,
		)
	return len(rows)


def after_migrate():
	"""Fields, renames, channels and backfills - everything a deploy needs besides the workflow
	itself (that one rewrites states and stays a deliberate `install`)."""
	if not frappe.db.table_exists("Sales Channel"):
		# Seen 2026-09-08: the first migrate after a module was added to modules.txt ran with the
		# module map still cached, so the DocType was not synced and the site was left half done.
		frappe.throw(
			"DocType 'Sales Channel' wurde nicht synchronisiert (Modul-Cache veraltet): "
			"`bench --site <site> clear-cache` und `bench --site <site> migrate` erneut ausführen."
		)
	create_custom_fields(CUSTOM_FIELDS, update=True)
	from ecommerce_integrations.shopify.doctype.shopify_account.shopify_account import setup_custom_fields

	if frappe.db.exists("Shopify Account"):
		setup_custom_fields()  # the Shopify fields lost their list filter flag on 2026-09-08
	copied = migrate_legacy_fields()
	channels = channel.ensure_channels_for_existing_accounts()
	payments = backfill_payment_status()
	orders = backfill_shopify_orders()
	print(
		f"b2c: renamed fields {copied or '-'}; sales channels created {channels or '-'}; "
		f"payment status derived for {payments}; channel contract backfilled on {orders} Shopify orders"
	)


def install():
	after_migrate()
	ensure_multisizer_item()
	ensure_states()
	ensure_actions()
	result = ensure_workflow()
	frappe.db.commit()
	return f"Workflow '{g.WORKFLOW_NAME}' {result}; {len(STATES)} states, {len(TRANSITIONS)} transitions"
