"""Shipping address plausibility for the B2C order gate, and the manual outcome of that gate.

Template (user decision 2026-09-03): Marello's OrderBundle/Workflow/AddressCheckAction. The
rules, in this order:

1. DHL Packstation (Germany only): the address must read "<Postnummer> Packstation <NNN>" in
   line 1 with an empty line 2 - the check normalises the order of the two tokens in place.
2. Street regex per country (Marello csvimport_yml -> shipping.address_regex) on line 1 and
   line 2 JOINED WITH A SPACE - Marello's getAdressMailing() builds exactly that string, and
   Shopify often delivers the house number in address2. Fallback for unknown countries is the
   German pattern.
3. House number the way Oro's request-order converter extracts it (CoreBundle
   AddressFormatter::extractHouseNumber, used by RfoBundle's AddressValidator): a bare number
   in line 2, otherwise a number at the end of line 1 (number-first countries: at its start).
   Oro refuses to convert a request order without one, so the gate must not let such an
   address through - the person fixes it here, before Adomio sees the order.

Anything that fails is reported, never silently fixed. The placeholder "Address 1" that the
Shopify import writes for an empty street counts as missing.
"""

import re

import frappe
from frappe.utils import cint

# From Marello's shipping.address_regex (PCRE -> Python: identical named-group syntax).
ADDRESS_REGEX = {
	"DE": re.compile(r"^(?P<address>\d*\D+\.*)(?P<number>[^a-zA-Z]?\D*\d+.*)$"),
	"AT": re.compile(r"^(?P<address>\d*\D+\.*)(?P<number>[^a-zA-Z]?\D*\d+.*)$"),
	"CH": re.compile(r"^(?P<address>\d*\D+\.*)(?P<number>[^a-zA-Z]?\D*\d+.*)$"),
	"NL": re.compile(r"^(?P<address>\d*\D+\.*)(?P<number>[^a-zA-Z]?\D*\d+.*)$"),
	"LU": re.compile(r"^(?P<number>[^a-zA-Z]?\d+),?\s*(?P<address>\d*\D+\.*\S*)$"),
	"FR": re.compile(r"^(?P<number>[^a-zA-Z]?\d+),?\s*(?P<address>\d*\D+\.*\S*)$"),
	"US": re.compile(
		r"^\s*(?:(?P<number>\d+[a-zA-Z]?(?:-\d+[a-zA-Z]?)?)\s+(?P<address>(?:[a-zA-Z0-9.\/\-\']+\s*)+)(?:[,\s]+(?P<unit>[^\s]+))?)|(?P<po_box>(?:PO|P\.O\.)\s+BOX\s+\d+)\s*$",
		re.I,
	),
	"CA": re.compile(
		r"^\s*(?:(?P<number>\d+[a-zA-Z]?(?:-\d+[a-zA-Z]?)?)\s+(?P<address>(?:[a-zA-Z0-9.\/\-\']+\s*)+)(?:[,\s]+(?P<unit>[^\s]+))?)|(?P<po_box>(?:PO|P\.O\.)\s+BOX\s+\d+)\s*$",
		re.I,
	),
}

# Countries whose house number precedes the street (Marello's regex set above; Oro derives
# the same thing from its address templates).
NUMBER_FIRST = ("LU", "FR", "US", "CA")

# Oro AddressFormatter::extractHouseNumber - the three patterns, verbatim.
HOUSE_NUMBER_LINE2 = re.compile(r"^\d+\s*[a-zA-Z]?$")
HOUSE_NUMBER_END = re.compile(r"\s+(\d+\s*[a-zA-Z]?)$")
HOUSE_NUMBER_START = re.compile(r"^(\d+\s*[a-zA-Z]?)[,\s]+")

PACKSTATION = re.compile(r"[Pp]ackstation\s*(?P<station>\d{3})")
POSTNUMMER = re.compile(r"(?<!\d)(?P<post>\d{6,10})(?!\d)")

# What shopify/customer._map_address_fields writes when Shopify sent no address1.
PLACEHOLDER_STREET = "Address 1"

# Address fields the form dialog "Adresse prüfen" may change.
EDITABLE_FIELDS = ("address_title", "address_line1", "address_line2", "pincode", "city", "country")

ACTION_CORRECTED = "Adresse korrigiert"
ACTION_CONFIRMED = "Adresse bestätigt"


def country_code(country_name):
	"""ISO2 of an ERPNext country name (Country.code is stored lowercase)."""
	if not country_name:
		return None
	code = frappe.db.get_value("Country", country_name, "code")
	return code.upper() if code else None


def normalize_packstation(line1, line2, code):
	"""Return (is_packstation, line1, line2). Only Germany has DHL Packstations; the tokens are
	put into DHL's order (Postnummer first, then "Packstation NNN") when both are present."""
	if code != "DE":
		return False, line1, line2
	combined = " ".join(filter(None, [line1, line2]))
	station = PACKSTATION.search(combined)
	if not station:
		return False, line1, line2
	post = POSTNUMMER.search(combined)
	if not post:
		# Packstation named, but no plausible Postnummer (6-10 digits): leave it for a human.
		return True, line1, line2
	return True, f"{post.group('post')} Packstation {station.group('station')}", ""


def street_line(line1, line2):
	"""Marello's getAdressMailing(): street and street2 joined with one space."""
	return " ".join(part for part in (line1, line2) if part)


def house_number(line1, line2, code):
	"""Oro's AddressFormatter::extractHouseNumber: line 2 when it is a bare number, otherwise
	the number at the end of line 1 (number-first countries: at its start, end as fallback).
	None when Oro would find nothing - its converter then refuses the request order."""
	if line2 and HOUSE_NUMBER_LINE2.match(line2):
		return line2
	if not line1:
		return None
	patterns = (HOUSE_NUMBER_START, HOUSE_NUMBER_END) if code in NUMBER_FIRST else (HOUSE_NUMBER_END, HOUSE_NUMBER_START)
	for pattern in patterns:
		match = pattern.search(line1)
		if match:
			return match.group(1).strip()
	return None


def check_lines(line1, line2, code):
	"""Pure check. Returns (ok, message, line1, line2) - the lines come back normalised (only
	the Packstation rule rewrites them; a house number in line 2 stays where it is, as it
	does in Marello)."""
	line1 = (line1 or "").strip()
	line2 = (line2 or "").strip()
	is_packstation, line1, line2 = normalize_packstation(line1, line2, code)
	if is_packstation:
		if POSTNUMMER.search(line1) and PACKSTATION.search(line1):
			return True, "", line1, line2
		return False, "Packstation ohne plausible Postnummer", line1, line2
	street1 = "" if line1 == PLACEHOLDER_STREET else line1
	if not street1:
		# Address.address_line1 is mandatory, so only the placeholder gets here: the street
		# has to be entered in line 1 whatever line 2 holds (Oro would read the placeholder).
		return False, "Straße fehlt", line1, line2
	pattern = ADDRESS_REGEX.get(code or "DE", ADDRESS_REGEX["DE"])
	if not pattern.match(street_line(street1, line2)):
		return False, "Straße/Hausnummer nicht erkannt", line1, line2
	if house_number(street1, line2, code) is None:
		return False, "Hausnummer nicht erkannt", line1, line2
	return True, "", line1, line2


def check_address(address_name, save_normalised=True):
	"""Check an ERPNext Address by name. Writes a normalised Packstation back when asked to."""
	if not address_name:
		return False, "Keine Lieferadresse"
	address = frappe.get_doc("Address", address_name)
	code = country_code(address.country)
	ok, message, line1, line2 = check_lines(address.address_line1, address.address_line2, code)
	if save_normalised and (line1 != (address.address_line1 or "") or line2 != (address.address_line2 or "")):
		address.db_set({"address_line1": line1, "address_line2": line2}, update_modified=False)
	if not address.pincode or not address.city:
		return False, "PLZ oder Ort fehlt"
	return ok, message


def refresh_display(so, address):
	"""A submitted order keeps a rendered copy of its addresses (validate does not run after
	submit, and nothing propagates from the Address) - re-render the copy so the form, the
	purchase order made from it and the print show the correction."""
	from frappe.contacts.doctype.address.address import get_address_display

	text = get_address_display(address.as_dict())
	values = {}
	if so.shipping_address_name == address.name:
		values["shipping_address"] = text
	if so.customer_address == address.name:
		values["address_display"] = text
	if values:
		so.db_set(values, update_modified=False)
	return text


def _apply_correction(address, values):
	"""Write the dialog's values onto the Address. Returns the list of changed fields with
	their old and new value."""
	changes = []
	for field in EDITABLE_FIELDS:
		if field not in values:
			continue
		new = (values.get(field) or "").strip()
		old = (address.get(field) or "").strip()
		if new != old:
			address.set(field, new)
			changes.append((field, old, new))
	return changes


@frappe.whitelist()
def resolve(sales_order, address=None, confirm=0):
	"""Outcome of the manual address check (form dialog "Adresse prüfen" on an order in the
	state "Adressprüfung"): saves the corrected shipping address in place, refreshes the address
	text on the submitted order, re-runs the check and takes the workflow action - "Adresse
	korrigiert" when the check passes now, "Adresse bestätigt" when the person vouches for the
	address despite the check (flag b2c_address_confirmed, the gate skips the check from then
	on). A failing check without confirmation saves the address but leaves the order where it
	is; the result tells the dialog why."""
	from frappe.model.workflow import apply_workflow

	from ecommerce_integrations.b2c import gates

	frappe.only_for(("System Manager", "Sales User", "Sales Manager"))
	so = frappe.get_doc("Sales Order", sales_order)
	so.check_permission("write")
	if so.docstatus != 1 or so.get(gates.STATE_FIELD) != gates.STATE_ADDRESS:
		frappe.throw(f"{so.name} steht nicht in der Adressprüfung (Zustand: {so.get(gates.STATE_FIELD) or '–'}).")
	if not so.shipping_address_name:
		frappe.throw(f"{so.name} hat keine Lieferadresse.")

	values = frappe.parse_json(address) or {}
	doc = frappe.get_doc("Address", so.shipping_address_name)
	changes = _apply_correction(doc, values)
	if changes:
		doc.save()
		gates.log_gate(
			so,
			"Lieferadresse korrigiert: "
			+ "; ".join(f"{field} „{old}“ → „{new}“" for field, old, new in changes),
		)

	ok, message = check_address(doc.name)
	doc = frappe.get_doc("Address", doc.name)  # the check may have normalised a Packstation
	display = refresh_display(so, doc)
	so.db_set("b2c_address_check", "OK" if ok else message, update_modified=False)

	confirm = cint(confirm)
	action = None
	if confirm:
		so.db_set("b2c_address_confirmed", 1, update_modified=False)
		gates.log_gate(so, "Adresse manuell bestätigt" + (f" (Prüfung: {message})" if not ok else ""))
		action = ACTION_CONFIRMED
	elif ok:
		action = ACTION_CORRECTED

	state = so.get(gates.STATE_FIELD)
	if action:
		# The workflow action itself triggers on_update_after_submit -> the gates run again.
		state = apply_workflow(so, action).get(gates.STATE_FIELD)

	return {
		"ok": ok,
		"message": message,
		"action": action,
		"state": state,
		"changed": [field for field, _old, _new in changes],
		"shipping_address": display,
	}
