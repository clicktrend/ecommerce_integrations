import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils.nestedset import get_root_of

from ecommerce_integrations.shopify.constants import CUSTOMER_ID_FIELD
from ecommerce_integrations.shopify.customer import ShopifyCustomer
from ecommerce_integrations.shopify.order import _order_address, create_sales_order

SHOP_CUSTOMER_ID = "999000222"


def _address(first, last, street, zip_code, city):
	return {"first_name": first, "last_name": last, "address1": street, "address2": "", "zip": zip_code,
		"city": city, "country": "Germany", "country_code": "DE"}


# The customer pays from home and has the parcel sent to the office - two places, one person.
HOME = _address("Erika", "Muster", "Buchenweg 3", "59065", "Hamm")
OFFICE = _address("Erika", "Muster", "Industriestraße 40", "59067", "Hamm")
MOVED = _address("Erika", "Muster", "Lindenallee 12", "44135", "Dortmund")
BUYER = {"id": SHOP_CUSTOMER_ID, "first_name": "Erika", "last_name": "Muster", "email": "erika@example.org"}


class _Setting:
	name = "z6wkr3-yk.myshopify.com"
	default_customer = None
	sales_order_series = None
	company = "_Test Company"


class TestBillingAddress(unittest.TestCase):
	"""B2C shadow 2026-09-12, finding U: the invoice address of a Shopify order could be its shipping address,
	and a returning customer's billing address was overwritten in place."""

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

	def _order(self, billing=HOME, shipping=OFFICE, customer=BUYER):
		return {"customer": dict(customer), "billing_address": billing, "shipping_address": shipping}

	def test_the_same_billing_address_is_found_again(self):
		first = self.customer.order_address("Erika Muster", HOME, "Billing")
		self.assertEqual(self.customer.order_address("Erika Muster", HOME, "Billing"), first)
		self.assertEqual(len(self.customer.get_customer_address_names("Billing")), 1)

	def test_a_new_billing_address_keeps_the_one_an_earlier_order_was_invoiced_to(self):
		first = self.customer.order_address("Erika Muster", HOME, "Billing")
		self.customer.update_existing_addresses(dict(BUYER, billing_address=MOVED))

		kept = frappe.db.get_value("Address", first, ["address_line1", "city", "pincode"], as_dict=True)
		self.assertEqual((kept.address_line1, kept.city, kept.pincode), ("Buchenweg 3", "Hamm", "59065"))
		self.assertEqual(len(self.customer.get_customer_address_names("Billing")), 2)

	def test_the_order_names_its_billing_address_never_its_shipping_address(self):
		# The shipping address is created first - the order in which the default lookup found it on prod.
		shipping = self.customer.order_address("Erika Muster", OFFICE, "Shipping")
		billing = self.customer.order_address("Erika Muster", HOME, "Billing")

		self.assertEqual(_order_address(self._order(), "Billing"), billing)
		self.assertEqual(_order_address(self._order(), "Shipping"), shipping)
		self.assertEqual(frappe.db.get_value("Address", billing, "address_type"), "Billing")

	def test_without_a_billing_address_the_default_address_of_the_customer_stands_in(self):
		order = self._order(billing=None, customer=dict(BUYER, default_address=MOVED))
		billing = _order_address(order, "Billing")
		self.assertEqual(frappe.db.get_value("Address", billing, ["city", "address_type"]), ("Dortmund", "Billing"))

	def test_a_nameless_customer_finds_its_billing_address_again(self):
		nameless = dict(BUYER, first_name="", last_name="")
		self.customer.update_existing_addresses(dict(nameless, billing_address=HOME))
		self.assertEqual(len(self.customer.get_customer_address_names("Billing")), 1)
		self.assertEqual(
			_order_address(self._order(customer=nameless), "Billing"),
			self.customer.get_customer_address_names("Billing")[0],
		)

	def test_guest_orders_keep_the_default(self):
		self.assertIsNone(_order_address({"customer": {}, "billing_address": HOME}, "Billing"))

	def test_the_sales_order_carries_both_addresses_explicitly(self):
		"""What the fix is for: customer_address on the Sales Order - the address its invoice is made out to."""
		self.customer.order_address("Erika Muster", OFFICE, "Shipping")
		self.customer.order_address("Erika Muster", HOME, "Billing")
		so = MagicMock(customer_address=None, shipping_address_name=None)
		real_get_doc = frappe.get_doc

		def get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict) and args[0].get("doctype") == "Sales Order":
				return so
			return real_get_doc(*args, **kwargs)

		order = dict(self._order(), id="999000222001", name="#99001", created_at="2026-09-14T10:00:00+02:00",
			currency="EUR", line_items=[])
		with (
			patch("ecommerce_integrations.shopify.order.get_order_items", return_value=[{"item_code": "X"}]),
			patch("ecommerce_integrations.shopify.order.get_order_taxes", return_value=[{"charge_type": "Actual"}]),
			patch("ecommerce_integrations.shopify.order.get_dummy_tax_category", return_value="dummy"),
			patch("ecommerce_integrations.shopify.order.price_list_for", return_value="Standard Selling"),
			patch.object(frappe, "get_doc", side_effect=get_doc),
		):
			self.assertIs(create_sales_order(order, _Setting()), so)

		self.assertEqual(frappe.db.get_value("Address", so.customer_address, ["city", "address_line1", "address_type"]),
			("Hamm", "Buchenweg 3", "Billing"))
		self.assertEqual(frappe.db.get_value("Address", so.shipping_address_name, ["address_line1", "address_type"]),
			("Industriestraße 40", "Shipping"))
		so.submit.assert_called_once()
