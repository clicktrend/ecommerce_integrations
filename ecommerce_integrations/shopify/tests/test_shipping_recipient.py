import unittest

import frappe
from erpnext.accounts.party import get_party_shipping_address
from frappe.utils.nestedset import get_root_of

from ecommerce_integrations.shopify.constants import CUSTOMER_ID_FIELD
from ecommerce_integrations.shopify.customer import ShopifyCustomer, _map_address_fields
from ecommerce_integrations.shopify.order import _order_shipping_address

SHOP_CUSTOMER_ID = "999000111"


def _address(first, last, street, zip_code, city):
	return {"first_name": first, "last_name": last, "address1": street, "address2": "", "zip": zip_code,
		"city": city, "country": "Germany", "country_code": "DE"}


# The ordering customer lives in one town; the gift goes to someone else in another.
BUYER = _address("Erika", "Muster", "Buchenweg 3", "59065", "Hamm")
GIFT = _address("Jonas", "Beispiel", "Lindenallee 12", "44135", "Dortmund")


class TestShippingAddressMapping(unittest.TestCase):
	"""B2C shadow 2026-09-11, finding S: the recipient of a gift order was lost at import."""

	def test_shipping_address_names_the_recipient(self):
		fields = _map_address_fields(GIFT, "Erika Muster", "Shipping", None)
		self.assertEqual(fields["address_title"], "Jonas Beispiel")

	def test_billing_address_keeps_the_customer(self):
		fields = _map_address_fields(GIFT, "Erika Muster", "Billing", None)
		self.assertEqual(fields["address_title"], "Erika Muster")

	def test_without_a_name_on_the_address_the_customer_stays(self):
		nameless = dict(GIFT, first_name="", last_name="")
		self.assertEqual(_map_address_fields(nameless, "Erika Muster", "Shipping", None)["address_title"], "Erika Muster")
		self.assertEqual(
			_map_address_fields(dict(nameless, name="Jonas Beispiel"), "Erika Muster", "Shipping", None)["address_title"],
			"Jonas Beispiel",
		)


class TestOrderShippingAddress(unittest.TestCase):
	"""One shipping address per place and recipient, found again or added - never overwritten."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.get_doc({
			"doctype": "Customer", "customer_name": "Erika Muster", CUSTOMER_ID_FIELD: SHOP_CUSTOMER_ID,
			"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
			"territory": get_root_of("Territory"),
		}).insert(ignore_permissions=True, ignore_mandatory=True)
		self.customer = ShopifyCustomer(customer_id=SHOP_CUSTOMER_ID)

	def tearDown(self):
		frappe.db.rollback()

	def _order(self, shipping):
		return {"customer": {"id": SHOP_CUSTOMER_ID, "first_name": "Erika", "last_name": "Muster",
			"email": "erika@example.org"}, "shipping_address": shipping}

	def test_same_place_and_recipient_is_found_again(self):
		first = self.customer.order_shipping_address("Erika Muster", GIFT)
		self.assertEqual(self.customer.order_shipping_address("Erika Muster", GIFT), first)
		self.assertEqual(len(self.customer.get_customer_address_names("Shipping")), 1)

	def test_a_second_order_to_another_place_keeps_the_first_address(self):
		first = self.customer.order_shipping_address("Erika Muster", BUYER)
		second = self.customer.order_shipping_address("Erika Muster", GIFT)

		self.assertNotEqual(first, second)
		kept = frappe.db.get_value("Address", first, ["address_title", "city", "pincode"], as_dict=True)
		self.assertEqual((kept.address_title, kept.city, kept.pincode), ("Erika Muster", "Hamm", "59065"))
		self.assertEqual(frappe.db.get_value("Address", second, "address_title"), "Jonas Beispiel")

	def test_update_of_a_returning_customer_adds_instead_of_overwriting(self):
		first = self.customer.order_shipping_address("Erika Muster", BUYER)
		self.customer.update_existing_addresses({"first_name": "Erika", "last_name": "Muster", "shipping_address": GIFT})

		self.assertEqual(frappe.db.get_value("Address", first, "city"), "Hamm")
		self.assertEqual(len(self.customer.get_customer_address_names("Shipping")), 2)

	def test_the_sales_order_gets_its_own_address_named_explicitly(self):
		self.customer.order_shipping_address("Erika Muster", BUYER)
		gift = self.customer.order_shipping_address("Erika Muster", GIFT)
		customer = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: SHOP_CUSTOMER_ID}, "name")

		# Why the order has to say it: with two shipping addresses ERPNext's default lookup names none.
		self.assertIsNone(get_party_shipping_address("Customer", customer))
		self.assertEqual(_order_shipping_address(self._order(GIFT)), gift)

	def test_guest_orders_keep_the_default(self):
		self.assertIsNone(_order_shipping_address({"customer": {}, "shipping_address": GIFT}))
