"""The sales channel of an order, and the interface every integration fulfils.

Every integration is a sales channel (user decision 2026-09-08, modelled after Marello: the
SalesChannel points at its integration, not the other way round). The B2C instance therefore
talks to its connectors through four contracts, none of which needs a Python import - the
Amazon app keeps its arm's length from this GPL fork, and a fourth shop must be a document
someone fills in, not a deployment:

1. Registration - `hooks.py` of the integration app::

       sales_channel_integrations = {"Amazon SP Account": {"code_field": "channel_code",
                                                            "channel_type": "Marketplace"}}

   That is what `Sales Channel.integration_type` may point at, and what creates a Sales
   Channel automatically when such an account is inserted (wildcard doc_events hook).

2. Write contract - the CONTRACT_FIELDS below on every Sales Order the connector creates.
   `validate_order` refuses an order that names a channel but skips the rest.

3. Read contract - the Sales Channel itself: company, revenue accounts, sender, logo,
   behaviour flags. Consumers (revenue, mails, gates, print formats) read `values()` and
   `address_check()`; nobody reads a connector account any more.

4. Shipment - listeners registered under `sales_channel_order_shipped` are told when a B2C
   order is shipped (`notify_shipped`); each one decides by field name whether the order is its own.
"""

import frappe
from frappe import _
from frappe.utils import cint

HOOK = "sales_channel_integrations"
SHIPPED_HOOK = "sales_channel_order_shipped"
DOCTYPE = "Sales Channel"
ORDER_FIELD = "sales_channel"

# What every connector writes on the Sales Order (the `integration_` prefix marks the contract).
CONTRACT_FIELDS = (
	ORDER_FIELD,
	"integration_order_id",
	"integration_ordered_at",
	"integration_priority",
	"integration_ship_by",
	"integration_payment_status",
	"integration_payment_gateway",
)
# Without these two an order cannot be worked: support finds it by the shop's number, the gates
# release it by the payment marker. The rest may be empty (a shop without deadlines or priorities).
REQUIRED_CONTRACT_FIELDS = ("integration_order_id", "integration_payment_status")

PRIORITY_NORMAL = "Normal"
PRIORITY_HIGH = "High"

# Brand settings that moved from the connector accounts onto the channel (2026-09-08). Kept as a
# list so the one-time copy in ensure_for_integration() can read them from an account by name.
BRAND_FIELDS = ("income_account", "income_account_at", "sender_email", "sender_name", "brand_logo")

DEFAULT_CHANNEL_TYPE = "Webshop"
# Marketplaces own the buyer's address (Amazon), a webshop's address goes through our check.
ADDRESS_CHECK_BY_TYPE = {"Webshop": 1, "Marketplace": 0, "POS": 0}


# --- registry ----------------------------------------------------------------------------------


def _first(value):
	"""frappe.get_hooks() turns every leaf into a list; the registry wants scalars."""
	if isinstance(value, list | tuple):
		return value[0] if value else None
	return value


def registry():
	"""{account doctype: {"code_field": str | None, "channel_type": str}} over all installed apps."""
	out = {}
	for doctype, entry in (frappe.get_hooks(HOOK) or {}).items():
		entry = entry or {}
		out[doctype] = {
			"code_field": _first(entry.get("code_field")),
			"channel_type": _first(entry.get("channel_type")) or DEFAULT_CHANNEL_TYPE,
		}
	return out


@frappe.whitelist()
def integration_doctypes():
	"""The account DocTypes a Sales Channel may point at (form picker + server validation)."""
	return sorted(registry())


# --- channel <-> integration -------------------------------------------------------------------


def for_integration(doctype, name):
	"""Name of the Sales Channel this account serves, or None."""
	if not (doctype and name):
		return None
	return frappe.db.get_value(DOCTYPE, {"integration_type": doctype, "integration": name}, "name")


def ensure_for_integration(doctype, name):
	"""Sales Channel of an account, created on first sight - the mapping the user asked for when
	an integration is set up. The code comes from the account's code field when the registry
	names one (Amazon: channel_code), otherwise from the account name."""
	existing = for_integration(doctype, name)
	if existing:
		return existing
	entry = registry().get(doctype)
	if entry is None:
		frappe.throw(_("{0} ist keine registrierte Integration (Hook {1}).").format(doctype, HOOK))
	# "*" instead of the meta: the brand columns stay in the table after they left the account's
	# DocType, and this copy is exactly the moment they move over.
	row = frappe.db.get_value(doctype, name, "*", as_dict=True) or frappe._dict()
	code = _unique_code((entry["code_field"] and row.get(entry["code_field"])) or code_from_name(name))
	channel_type = entry["channel_type"]
	values = {
		"doctype": DOCTYPE,
		"code": code,
		# The brand name where the account carries one (Amazon: account_name, a shop: its sender),
		# otherwise the account name; both are meant to be edited on the channel afterwards.
		"channel_name": row.get("account_name") or row.get("sender_name") or name,
		"channel_type": channel_type,
		"enabled": 1,
		"company": row.get("company") or frappe.defaults.get_global_default("company"),
		"integration_type": doctype,
		"integration": name,
		"address_check": ADDRESS_CHECK_BY_TYPE.get(channel_type, 1),
	}
	for field in BRAND_FIELDS:
		if row.get(field):
			values[field] = row.get(field)
	doc = frappe.get_doc(values)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def code_from_name(name):
	"""Default channel code from an account name: a Shopify domain loses its suffix, and the
	result is a scrubbed identifier (`mit-bildgravur-de.myshopify.com` -> `mit_bildgravur_de`).
	The code is meant to be renamed to the Marello/Oro one afterwards (allow_rename)."""
	base = (name or "").strip().lower()
	for suffix in (".myshopify.com",):
		if base.endswith(suffix):
			base = base[: -len(suffix)]
	return frappe.scrub(base.replace(".", "_")) or "channel"


def _unique_code(code):
	candidate = code
	counter = 1
	while frappe.db.exists(DOCTYPE, candidate):
		counter += 1
		candidate = f"{code}-{counter}"
	return candidate


def on_integration_insert(doc, method=None):
	"""doc_events["*"]["after_insert"]: a freshly saved account gets its channel."""
	if doc.doctype in registry():
		ensure_for_integration(doc.doctype, doc.name)


def ensure_channels_for_existing_accounts():
	"""Migration helper: every account of every registered integration has a channel afterwards."""
	created = []
	for doctype in registry():
		if not frappe.db.exists("DocType", doctype):
			continue
		for name in frappe.get_all(doctype, pluck="name"):
			if not for_integration(doctype, name):
				created.append(ensure_for_integration(doctype, name))
	return created


# --- the channel of an order -------------------------------------------------------------------


def of_order(so):
	"""Sales Channel document of an order, or None for an order without one (manual)."""
	name = so.get(ORDER_FIELD)
	if not name:
		return None
	try:
		return frappe.get_cached_doc(DOCTYPE, name)
	except frappe.DoesNotExistError:
		return None


def values(so, *fields):
	"""Brand settings of the order's channel; an order without a channel yields an empty dict, so
	every caller can fall back to its own default."""
	doc = of_order(so)
	if not doc:
		return {}
	return {field: doc.get(field) for field in fields}


def address_check(so):
	"""Whether the B2C workflow checks this order's address (channel flag; no channel = no check)."""
	doc = of_order(so)
	return bool(doc and cint(doc.get("address_check")))


def is_high_priority(so):
	return (so.get("integration_priority") or "") == PRIORITY_HIGH


# --- contract guard ----------------------------------------------------------------------------


def validate_order(doc, method=None):
	"""Sales Order.validate: an order that names a channel carries the contract. Loud on purpose -
	a connector that forgets the payment marker would otherwise release or block orders silently."""
	if not doc.get(ORDER_FIELD):
		return
	missing = [field for field in REQUIRED_CONTRACT_FIELDS if not doc.get(field)]
	if missing:
		frappe.throw(
			_("Auftrag aus Sales Channel {0} ohne {1}: der Connector muss den Kanalvertrag vollständig schreiben.").format(
				doc.get(ORDER_FIELD), ", ".join(missing)
			)
		)
	if not doc.get("integration_priority"):
		doc.integration_priority = PRIORITY_NORMAL


# --- shipment listeners ------------------------------------------------------------------------


def notify_shipped(so, tracking_number=None, carrier=None):
	"""Tell every registered listener that a B2C order shipped. A listener's failure is logged on
	the order and never stops the others - the shipment bookkeeping in ERPNext is already done."""
	results = {}
	for path in frappe.get_hooks(SHIPPED_HOOK) or []:
		try:
			results[path] = frappe.get_attr(path)(so, tracking_number=tracking_number, carrier=carrier)
		except Exception as exc:
			frappe.log_error(title=f"B2C shipped listener {path} for {so.name}", message=frappe.get_traceback())
			so.add_comment("Info", f"B2C-Workflow: Versand-Listener {path} fehlgeschlagen: {exc}")
			results[path] = None
	return results
