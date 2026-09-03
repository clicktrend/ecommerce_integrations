"""Shipping address plausibility, ported from Marello's OrderBundle/Workflow/AddressCheckAction.

Two rules, applied in this order:
1. DHL Packstation (Germany only): the address must read "<Postnummer> Packstation <NNN>" in
   line 1 with an empty line 2 - the check normalises the order of the two tokens in place.
2. Street regex per country (Marello csvimport_yml -> shipping.address_regex): line 1 must
   carry a street name AND a house number; the fallback for unknown countries is the German
   pattern. Anything that fails is reported, never silently fixed.
"""

import re

import frappe

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

PACKSTATION = re.compile(r"[Pp]ackstation\s*(?P<station>\d{3})")
POSTNUMMER = re.compile(r"(?<!\d)(?P<post>\d{6,10})(?!\d)")


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


def check_lines(line1, line2, code):
	"""Pure check. Returns (ok, message, line1, line2) - the lines come back normalised."""
	line1 = (line1 or "").strip()
	line2 = (line2 or "").strip()
	is_packstation, line1, line2 = normalize_packstation(line1, line2, code)
	if is_packstation:
		if POSTNUMMER.search(line1) and PACKSTATION.search(line1):
			return True, "", line1, line2
		return False, "Packstation ohne plausible Postnummer", line1, line2
	if not line1:
		return False, "Straße fehlt", line1, line2
	pattern = ADDRESS_REGEX.get(code or "DE", ADDRESS_REGEX["DE"])
	if not pattern.match(line1):
		return False, "Straße/Hausnummer nicht erkannt", line1, line2
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
