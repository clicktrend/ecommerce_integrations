from typing import Any

import frappe
from frappe import _
from frappe.utils import cstr, validate_phone_number

from ecommerce_integrations.controllers.customer import EcommerceCustomer
from ecommerce_integrations.shopify.constants import (
	ADDRESS_ID_FIELD,
	CONTACT_MARKETING_CONSENT_AT_FIELD,
	CONTACT_MARKETING_OPT_IN_LEVEL_FIELD,
	CONTACT_MARKETING_STATE_FIELD,
	CUSTOMER_ID_FIELD,
	MODULE_NAME,
)
from ecommerce_integrations.shopify.utils import get_company_shopify_account, to_site_datetime

# The only email_marketing_consent.state that means "may be written to".
SUBSCRIBED = "subscribed"

# What makes two addresses of one type the same place for the same person.
ADDRESS_MATCH_FIELDS = ("address_title", "address_line1", "address_line2", "city", "pincode", "country")


class ShopifyCustomer(EcommerceCustomer):
	def __init__(self, customer_id: str):
		super().__init__(customer_id, CUSTOMER_ID_FIELD, MODULE_NAME)

	def sync_customer(self, customer: dict[str, Any], customer_group: str) -> None:
		"""Create Customer in ERPNext using shopify's Customer dict."""

		customer_name = customer_display_name(customer)

		super().sync_customer(customer_name, customer_group, company=customer.get("company"))

		billing_address = order_billing_address(customer.get("billing_address"), customer)
		shipping_address = customer.get("shipping_address", {})

		if billing_address:
			self.create_customer_address(
				customer_name, billing_address, address_type="Billing", email=customer.get("email")
			)
		if shipping_address:
			self.create_customer_address(
				customer_name, shipping_address, address_type="Shipping", email=customer.get("email")
			)

		self.create_customer_contact(customer)

	def create_customer_address(
		self,
		customer_name,
		shopify_address: dict[str, Any],
		address_type: str = "Billing",
		email: str | None = None,
	) -> str:
		"""Create customer address(es) using Customer dict provided by shopify. Returns the address name."""
		address_fields = _map_address_fields(shopify_address, customer_name, address_type, email)
		return super().create_customer_address(address_fields)

	def update_existing_addresses(self, customer):
		customer_name = customer_display_name(customer)
		email = customer.get("email")
		billing_address = order_billing_address(customer.get("billing_address"), customer)

		# Both found or added, never updated in place - see order_address.
		self.order_address(customer_name, billing_address, "Billing", email)
		self.order_address(customer_name, customer.get("shipping_address"), "Shipping", email)

	def order_address(
		self, customer_name, shopify_address: dict[str, Any] | None, address_type: str, email: str | None = None
	) -> str | None:
		"""The billing or shipping address of one order: the customer's address of that type with the same
		person and place, or a new one. Returns the address name for the Sales Order.

		The upstream code overwrote the customer's one address of each type with every order, so an earlier
		order - its invoice and the dropship purchase order made from it - moved to the next order's address
		(B2C shadow 2026-09-11, finding S for shipping; 2026-09-12, finding U for billing)."""
		if not shopify_address:
			return None
		wanted = _map_address_fields(shopify_address, customer_name, address_type, email)
		for name in self.get_customer_address_names(address_type):
			current = frappe.db.get_value("Address", name, list(ADDRESS_MATCH_FIELDS), as_dict=True) or {}
			if all(cstr(current.get(f)).strip() == cstr(wanted.get(f)).strip() for f in ADDRESS_MATCH_FIELDS):
				return name
		return self.create_customer_address(customer_name, shopify_address, address_type, email)

	def create_customer_contact(self, shopify_customer: dict[str, Any]) -> None:
		if not (shopify_customer.get("first_name") and shopify_customer.get("email")):
			return

		_ensure_consent_fields()
		contact_fields = {
			"status": "Passive",
			"first_name": shopify_customer.get("first_name"),
			"last_name": shopify_customer.get("last_name"),
			**marketing_consent_fields(shopify_customer),
		}

		if shopify_customer.get("email"):
			contact_fields["email_ids"] = [{"email_id": shopify_customer.get("email"), "is_primary": True}]

		phone_no = shopify_customer.get("phone") or shopify_customer.get("default_address", {}).get("phone")

		if validate_phone_number(phone_no, throw=False):
			contact_fields["phone_nos"] = [{"phone": phone_no, "is_primary_phone": True}]

		super().create_customer_contact(contact_fields)

	def get_customer_contact_name(self) -> str | None:
		"""Name of the (oldest) Contact linked to this customer, or None."""
		try:
			customer_name = self.get_customer_doc().name
		except frappe.DoesNotExistError:
			return None

		contacts = frappe.get_all(
			"Contact",
			filters=[
				["Dynamic Link", "link_doctype", "=", "Customer"],
				["Dynamic Link", "link_name", "=", customer_name],
			],
			pluck="name",
			order_by="creation asc",
			limit=1,
		)
		return contacts[0] if contacts else None

	def sync_marketing_consent(self, shopify_customer: dict[str, Any]) -> None:
		"""Carry the current consent state to the linked Contact.

		Called for customers that already exist, i.e. on every order after the first. Consent
		changes between orders (the customer subscribes or unsubscribes in the shop); the
		contact has to follow, otherwise ERPNext keeps writing to someone who opted out.
		Creates the contact when an earlier import left none behind.
		"""
		contact_name = self.get_customer_contact_name()
		if not contact_name:
			self.create_customer_contact(shopify_customer)
			return

		_ensure_consent_fields()
		fields = marketing_consent_fields(shopify_customer)
		current = frappe.db.get_value("Contact", contact_name, list(fields), as_dict=True) or {}
		if all(current.get(key) == value for key, value in fields.items()):
			return

		frappe.db.set_value("Contact", contact_name, fields)


def marketing_consent_fields(shopify_customer: dict[str, Any]) -> dict[str, Any]:
	"""Map Shopify's ``email_marketing_consent`` to the Contact fields.

	Shopify records consent as a state ("subscribed", "not_subscribed", "unsubscribed",
	"pending", "redacted", "invalid"), an opt-in level ("single_opt_in", "confirmed_opt_in",
	"unknown") and the time it last changed. All three are kept, because a bare checkbox is
	no evidence of consent - the timestamp and the level are (GDPR Art. 5(2)).

	``unsubscribed`` is derived and fail-safe: only an explicit "subscribed" clears it. A
	missing block and a pending double opt-in leave the contact unsubscribed. The legacy
	``accepts_marketing`` flag is ignored on purpose: it carries no timestamp, and API
	2024-01 no longer sends it - reading it made every contact unsubscribed silently.
	"""
	consent = shopify_customer.get("email_marketing_consent") or {}
	state = consent.get("state")

	return {
		CONTACT_MARKETING_STATE_FIELD: state,
		CONTACT_MARKETING_OPT_IN_LEVEL_FIELD: consent.get("opt_in_level"),
		CONTACT_MARKETING_CONSENT_AT_FIELD: to_site_datetime(consent.get("consent_updated_at")),
		"unsubscribed": 0 if state == SUBSCRIBED else 1,
	}


def _ensure_consent_fields() -> None:
	"""Fail loudly when the Contact fields are missing, instead of dropping the evidence.

	``frappe.get_doc(...).insert()`` ignores unknown keys silently; ``frappe.db.set_value``
	would raise an opaque SQL error. Both would leave the consent unrecorded.
	"""
	meta = frappe.get_meta("Contact")
	if not meta.has_field(CONTACT_MARKETING_STATE_FIELD):
		frappe.throw(
			_(
				"Shopify marketing consent fields are missing on Contact. "
				"Save the enabled Shopify Account or run bench migrate to create them."
			)
		)


def customer_display_name(shopify_customer) -> str | None:
	"""First and last name of a Shopify customer, else the email: the name on the Customer and on its billing
	addresses. One derivation for creating and for finding an address again - two made a nameless customer's
	address unfindable."""
	name = (cstr(shopify_customer.get("first_name")) + " " + cstr(shopify_customer.get("last_name"))).strip()
	return name or shopify_customer.get("email")


def order_billing_address(billing_address, shopify_customer):
	"""The billing address of an order: the order's own, else the customer's default address in Shopify."""
	return billing_address or shopify_customer.get("default_address")


def _recipient(shopify_address) -> str:
	"""The person named on a Shopify address (first + last name, else the combined name)."""
	first = cstr(shopify_address.get("first_name")).strip()
	last = cstr(shopify_address.get("last_name")).strip()
	return " ".join(filter(None, (first, last))) or cstr(shopify_address.get("name")).strip()


def _map_address_fields(shopify_address, customer_name, address_type, email):
	"""returns dict with shopify address fields mapped to equivalent ERPNext fields"""
	address_fields = {
		# A shipping address names the recipient, who is not the ordering customer when the order goes to someone
		# else. Oro's erpnext connector reads the recipient from address_title (B2C shadow 2026-09-11, finding S:
		# 11 of 128 orders). A billing address keeps the customer's name.
		"address_title": (_recipient(shopify_address) if address_type == "Shipping" else "") or customer_name,
		"address_type": address_type,
		ADDRESS_ID_FIELD: shopify_address.get("id"),
		"address_line1": shopify_address.get("address1") or "Address 1",
		"address_line2": shopify_address.get("address2"),
		"city": shopify_address.get("city"),
		"state": shopify_address.get("province"),
		"pincode": shopify_address.get("zip"),
		"country": shopify_address.get("country"),
		"email_id": email,
	}

	phone = shopify_address.get("phone")
	if validate_phone_number(phone, throw=False):
		address_fields["phone"] = phone

	return address_fields
