import json
from typing import Literal, Optional

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, nowdate
from shopify.collection import PaginatedIterator
from shopify.resources import Order

from ecommerce_integrations.shopify.connection import get_temp_session_context
from ecommerce_integrations.shopify.constants import (
	CUSTOMER_ID_FIELD,
	EVENT_MAPPER,
	ORDER_ID_FIELD,
	ORDER_ITEM_DISCOUNT_FIELD,
	ORDER_ITEM_PERSONALIZED_FIELD,
	ORDER_ITEM_PROPERTIES_FIELD,
	ORDER_ACCOUNT_FIELD,
	ORDER_FINANCIAL_STATUS_FIELD,
	ORDER_NUMBER_FIELD,
	ORDER_PAYMENT_GATEWAY_FIELD,
	ORDER_PLACED_AT_FIELD,
	ORDER_STATUS_FIELD,
	ACCOUNT_DOCTYPE,
	# SETTING_DOCTYPE,
)
from ecommerce_integrations.shopify.customer import ShopifyCustomer
from ecommerce_integrations.shopify.product import create_items_if_not_exist, get_item_code
from ecommerce_integrations.shopify.utils import (
	create_shopify_log,
	get_user_shopify_account,
	to_site_datetime,
)
from ecommerce_integrations.utils.price_list import get_dummy_price_list
from ecommerce_integrations.utils.taxation import get_dummy_tax_category

DEFAULT_TAX_FIELDS = {
	"sales_tax": "default_sales_tax_account",
	"shipping": "default_shipping_charges_account",
}


def sync_sales_order(payload, request_id=None, shopify_account=None):
	order = payload

	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id
	
	if isinstance(shopify_account, str):
		shopify_account = frappe.get_doc("Shopify Account", shopify_account)

	shopify_account_name = shopify_account.name if shopify_account else None
	if frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: cstr(order["id"])}):
		create_shopify_log(status="Invalid", message="Sales order already exists, not synced", shopify_account=shopify_account_name)
		return
	try:
		shopify_customer = order.get("customer") if order.get("customer") is not None else {}
		shopify_customer["billing_address"] = order.get("billing_address", "")
		shopify_customer["shipping_address"] = order.get("shipping_address", "")
		customer_id = shopify_customer.get("id")
		if customer_id:
			customer = ShopifyCustomer(customer_id=customer_id)
			# Add company to shopify_customer for multi-company setups
			shopify_customer['company'] = shopify_account.company
			if not customer.is_synced():
				customer.sync_customer(customer=shopify_customer, customer_group=shopify_account.customer_group)
			else:
				customer.update_existing_addresses(shopify_customer)
				# Consent changes between orders; the contact has to follow every time.
				customer.sync_marketing_consent(shopify_customer)

		create_items_if_not_exist(order, company=shopify_account.company)

		create_order(order, shopify_account)
	except frappe.UniqueValidationError:
		# Two workers processed the same webhook (Shopify retry) at once: the other one won the
		# unique index on shopify_order_id / shopify_customer_id. Same outcome as the check above.
		create_shopify_log(
			status="Invalid",
			message="Sales order already exists (concurrent retry), not synced",
			rollback=True,
			shopify_account=shopify_account_name,
		)
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True, shopify_account=shopify_account_name)
	else:
		create_shopify_log(status="Success", shopify_account=shopify_account_name)


def create_order(order, setting, company=None):
	# local import to avoid circular dependencies
	from ecommerce_integrations.shopify.fulfillment import create_delivery_note
	from ecommerce_integrations.shopify.invoice import create_sales_invoice

	so = create_sales_order(order, setting, company)
	if so:
		if order.get("financial_status") == "paid":
			create_sales_invoice(order, setting, so)

		if order.get("fulfillments"):
			create_delivery_note(order, setting, so)


def _placed_at(value):
	return to_site_datetime(value)


def _delivery_date(created_at):
	"""The order date, but never before today.

	The date is copied onto the dropship purchase order as "Required By", and ERPNext
	refuses a Required By before the purchase order's own date. An order imported later
	than the day it was placed - catch-up after an outage, a replay - therefore failed
	at the very end of its import (replay of #10288/#10261, 2026-09-02).
	"""
	placed = getdate(created_at) if created_at else None
	today = getdate(nowdate())

	return max(placed, today) if placed else today


def _missing_lines(order_items):
	"""Lines of the shopify order whose item the site cannot resolve (see get_order_items)."""
	missing = []
	for shopify_item in order_items or []:
		if not shopify_item.get("product_exists") or not get_item_code(shopify_item):
			missing.append(shopify_item)

	return missing


def create_sales_order(shopify_order, setting, company=None):
	customer = setting.default_customer
	if shopify_order.get("customer", {}):
		if customer_id := shopify_order.get("customer", {}).get("id"):
			customer = frappe.db.get_value("Customer", {CUSTOMER_ID_FIELD: customer_id}, "name")

	so = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: shopify_order.get("id")}, "name")

	if not so:
		delivery_date = _delivery_date(shopify_order.get("created_at"))
		items = get_order_items(
			shopify_order.get("line_items"),
			setting,
			delivery_date,
			taxes_inclusive=shopify_order.get("taxes_included"),
		)

		if not items:
			message = (
				"Following items exist in the shopify order but relevant records were"
				" not found in the shopify Product master"
			)
			message += "\n" + ", ".join(
				f"{line.get('sku') or line.get('title')} (variant {line.get('variant_id')})"
				for line in _missing_lines(shopify_order.get("line_items"))
			)

			# Raise instead of logging here: sync_sales_order() closes its run with a
			# "Success" log on the same request id, which used to overwrite the "Error"
			# written at this point - the order looked imported without any sales order.
			frappe.throw(message)

		taxes = get_order_taxes(shopify_order, setting, items)
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				# Channel code + year: the counter restarts every year (5 digits per year and channel),
				# same pattern as the Amazon app (SO-AMZ-.YYYY.-). User decision 2026-09-03.
				"naming_series": setting.sales_order_series or "SO-SHP-.YYYY.-",
				ORDER_ID_FIELD: str(shopify_order.get("id")),
				ORDER_NUMBER_FIELD: shopify_order.get("name"),
				# Head facts for the freight contract. Shopify has no delivery deadline and no
				# priority flag - ship_by and is_prio are Amazon concepts and stay empty here.
				ORDER_ACCOUNT_FIELD: setting.name,
				ORDER_FINANCIAL_STATUS_FIELD: shopify_order.get("financial_status"),
				ORDER_PAYMENT_GATEWAY_FIELD: ", ".join(shopify_order.get("payment_gateway_names") or []),
				ORDER_PLACED_AT_FIELD: _placed_at(shopify_order.get("created_at")),
				"customer": customer,
				"transaction_date": getdate(shopify_order.get("created_at")) or nowdate(),
				"delivery_date": delivery_date,
				"company": setting.company,
				"currency": shopify_order.get("currency"),
				"selling_price_list": get_dummy_price_list(),
				"ignore_pricing_rule": 1,
				"items": items,
				"taxes": taxes,
				"tax_category": get_dummy_tax_category(),
			}
		)

		if company:
			so.update({"company": company, "status": "Draft"})
		so.flags.ignore_mandatory = True
		so.flags.shopiy_order_json = json.dumps(shopify_order)
		so.save(ignore_permissions=True)
		so.submit()

		if shopify_order.get("note"):
			so.add_comment(text=f"Order Note: {shopify_order.get('note')}")

	else:
		so = frappe.get_doc("Sales Order", so)

	return so


def get_order_items(order_items, setting, delivery_date, taxes_inclusive):
	items = []
	all_product_exists = True
	product_not_exists = []

	for shopify_item in order_items:
		if not shopify_item.get("product_exists"):
			all_product_exists = False
			product_not_exists.append(
				{"title": shopify_item.get("title"), ORDER_ID_FIELD: shopify_item.get("id")}
			)
			continue

		if all_product_exists:
			item_code = get_item_code(shopify_item)
			if not item_code:
				# The variant is unknown here (deleted or renamed in the shop since the order,
				# never synced): without this guard the line went through as an order without
				# item code - ignore_mandatory lets it save and submit - and the dropship
				# purchase order silently never came. Replay of #10263, 2026-09-02.
				all_product_exists = False
				product_not_exists.append(
					{
						"title": shopify_item.get("title"),
						"sku": shopify_item.get("sku"),
						"variant_id": shopify_item.get("variant_id"),
						ORDER_ID_FIELD: shopify_item.get("id"),
					}
				)
				items = []
				continue
			items.append(
				{
					"item_code": item_code,
					# Shopify line item names carry the full SEO title and blow past Frappe's 140
					# character limit, which aborts the order import (see _item_name in product.py).
					"item_name": cstr(shopify_item.get("name")).strip()[:140],
					"rate": _get_item_price(shopify_item, taxes_inclusive),
					"delivery_date": delivery_date,
					"qty": shopify_item.get("quantity"),
					"stock_uom": shopify_item.get("uom") or "Nos",
					"warehouse": setting.warehouse,
					ORDER_ITEM_DISCOUNT_FIELD: (
						_get_total_discount(shopify_item) / cint(shopify_item.get("quantity"))
					),
					ORDER_ITEM_PROPERTIES_FIELD: _get_item_properties(shopify_item),
					ORDER_ITEM_PERSONALIZED_FIELD: _is_personalized(shopify_item),
				}
			)
		else:
			items = []

	return items


def _get_item_properties(line_item) -> str | None:
	"""Return Shopify line item properties as JSON, verbatim.

	Properties carry the per-order personalisation (engraving text, font choice).
	They are stored unparsed on purpose: the meaning of a property is defined
	downstream, so anything interpreted here would have to be kept in sync twice.
	"""
	properties = line_item.get("properties") or []
	return json.dumps(properties, ensure_ascii=False) if properties else None


def _is_personalized(line_item) -> int:
	"""1 when the buyer chose at least one option on the line (engraving text, font, box).

	A leading underscore only hides a property in the shop's cart ("_Schriftart" and
	"Schriftart" are the same choice); the personalization plugin's bookkeeping is told
	apart by its "pplr" prefix ("_pplr_customization_id", "__pplr_line_ref"). Empty values
	are unfilled form fields. A derived flag for the items grid and for filtering - the
	properties JSON stays the truth.
	"""
	for prop in line_item.get("properties") or []:
		name = cstr(prop.get("name")).strip()
		if name and not _is_plugin_bookkeeping(name) and cstr(prop.get("value")).strip():
			return 1

	return 0


def _is_plugin_bookkeeping(name: str) -> bool:
	return name.lstrip("_").lower().startswith("pplr")


def _get_item_price(line_item, taxes_inclusive: bool) -> float:
	price = flt(line_item.get("price"))
	qty = cint(line_item.get("quantity"))

	# remove line item level discounts
	total_discount = _get_total_discount(line_item)

	if not taxes_inclusive:
		return price - (total_discount / qty)

	total_taxes = 0.0
	for tax in line_item.get("tax_lines"):
		total_taxes += flt(tax.get("price"))

	return price - (total_taxes + total_discount) / qty


def _get_total_discount(line_item) -> float:
	discount_allocations = line_item.get("discount_allocations") or []
	return sum(flt(discount.get("amount")) for discount in discount_allocations)


def get_order_taxes(shopify_order, setting, items):
	taxes = []
	line_items = shopify_order.get("line_items")

	for line_item in line_items:
		item_code = get_item_code(line_item)
		for tax in line_item.get("tax_lines"):
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax, setting, charge_type="sales_tax"),
					"description": (
						get_tax_account_description(tax, setting)
						or f"{tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
					),
					"tax_amount": tax.get("price"),
					"included_in_print_rate": 0,
					"cost_center": setting.cost_center,
					"item_wise_tax_detail": {item_code: [flt(tax.get("rate")) * 100, flt(tax.get("price"))]},
					"dont_recompute_tax": 1,
				}
			)

	update_taxes_with_shipping_lines(
		taxes,
		shopify_order.get("shipping_lines"),
		setting,
		items,
		taxes_inclusive=shopify_order.get("taxes_included"),
	)

	if cint(setting.consolidate_taxes):
		taxes = consolidate_order_taxes(taxes)

	for row in taxes:
		tax_detail = row.get("item_wise_tax_detail")
		if isinstance(tax_detail, dict):
			row["item_wise_tax_detail"] = json.dumps(tax_detail)

	return taxes


def consolidate_order_taxes(taxes):
	tax_account_wise_data = {}
	for tax in taxes:
		account_head = tax["account_head"]
		tax_account_wise_data.setdefault(
			account_head,
			{
				"charge_type": "Actual",
				"account_head": account_head,
				"description": tax.get("description"),
				"cost_center": tax.get("cost_center"),
				"included_in_print_rate": 0,
				"dont_recompute_tax": 1,
				"tax_amount": 0,
				"item_wise_tax_detail": {},
			},
		)
		tax_account_wise_data[account_head]["tax_amount"] += flt(tax.get("tax_amount"))
		if tax.get("item_wise_tax_detail"):
			tax_account_wise_data[account_head]["item_wise_tax_detail"].update(tax["item_wise_tax_detail"])

	return tax_account_wise_data.values()


def get_tax_account_head(tax, setting, charge_type: Literal["shipping", "sales_tax"] | None = None):
	tax_title = str(tax.get("title"))

	tax_account = frappe.db.get_value(
		"Shopify Tax Account",
		{"parent": setting.name, "shopify_tax": tax_title},
		"tax_account",
	)

	if not tax_account and charge_type:
		tax_account = frappe.db.get_value(ACCOUNT_DOCTYPE, setting.name, DEFAULT_TAX_FIELDS[charge_type])

	if not tax_account:
		frappe.throw(_("Tax Account not specified for Shopify Tax {0}").format(tax.get("title")))

	return tax_account


def get_tax_account_description(tax, setting):
	tax_title = tax.get("title")

	tax_description = frappe.db.get_value(
		"Shopify Tax Account",
		{"parent": setting.name, "shopify_tax": tax_title},
		"tax_description",
	)

	return tax_description


def update_taxes_with_shipping_lines(taxes, shipping_lines, setting, items, taxes_inclusive=False):
	"""Shipping lines represents the shipping details,
	each such shipping detail consists of a list of tax_lines"""
	shipping_as_item = cint(setting.add_shipping_as_item) and setting.shipping_item
	for shipping_charge in shipping_lines:
		if shipping_charge.get("price"):
			shipping_discounts = shipping_charge.get("discount_allocations") or []
			total_discount = sum(flt(discount.get("amount")) for discount in shipping_discounts)

			shipping_taxes = shipping_charge.get("tax_lines") or []
			total_tax = sum(flt(discount.get("price")) for discount in shipping_taxes)

			shipping_charge_amount = flt(shipping_charge["price"]) - flt(total_discount)
			if bool(taxes_inclusive):
				shipping_charge_amount -= total_tax

			if shipping_as_item:
				items.append(
					{
						"item_code": setting.shipping_item,
						"rate": shipping_charge_amount,
						"delivery_date": items[-1]["delivery_date"] if items else nowdate(),
						"qty": 1,
						"stock_uom": "Nos",
						"warehouse": setting.warehouse,
					}
				)
			else:
				taxes.append(
					{
						"charge_type": "Actual",
						"account_head": get_tax_account_head(shipping_charge, setting, charge_type="shipping"),
						"description": get_tax_account_description(shipping_charge, setting)
						or shipping_charge["title"],
						"tax_amount": shipping_charge_amount,
						"cost_center": setting.cost_center,
					}
				)

		for tax in shipping_charge.get("tax_lines"):
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": get_tax_account_head(tax, setting, charge_type="sales_tax"),
					"description": (
						get_tax_account_description(tax, setting)
						or f"{tax.get('title')} - {tax.get('rate') * 100.0:.2f}%"
					),
					"tax_amount": tax["price"],
					"cost_center": setting.cost_center,
					"item_wise_tax_detail": {
						setting.shipping_item: [flt(tax.get("rate")) * 100, flt(tax.get("price"))]
					}
					if shipping_as_item
					else {},
					"dont_recompute_tax": 1,
				}
			)


def get_sales_order(order_id):
	"""Get ERPNext sales order using shopify order id."""
	sales_order = frappe.db.get_value("Sales Order", filters={ORDER_ID_FIELD: order_id})
	if sales_order:
		return frappe.get_doc("Sales Order", sales_order)


def cancel_order(payload, request_id=None, shopify_account=None):
	"""Called by order/cancelled event.

	When shopify order is cancelled there could be many different someone handles it.

	Updates document with custom field showing order status.

	IF sales invoice / delivery notes are not generated against an order, then cancel it.
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	order = payload
	# The webhook hands the account document through; a name (CLI, replay) is loaded here,
	# same as in sync_sales_order().
	if isinstance(shopify_account, str):
		shopify_account = frappe.get_doc("Shopify Account", shopify_account)
	shopify_account_name = shopify_account.name if shopify_account else None

	try:
		order_id = order["id"]
		order_status = order["financial_status"]

		sales_order = get_sales_order(order_id)

		if not sales_order:
			create_shopify_log(status="Invalid", message="Sales Order does not exist", shopify_account=shopify_account_name)
			return

		sales_invoice = frappe.db.get_value("Sales Invoice", filters={ORDER_ID_FIELD: order_id})
		delivery_notes = frappe.db.get_list("Delivery Note", filters={ORDER_ID_FIELD: order_id})

		if sales_invoice:
			frappe.db.set_value("Sales Invoice", sales_invoice, ORDER_STATUS_FIELD, order_status)

		for dn in delivery_notes:
			frappe.db.set_value("Delivery Note", dn.name, ORDER_STATUS_FIELD, order_status)

		if not sales_invoice and not delivery_notes and sales_order.docstatus == 1:
			sales_order.cancel()
		else:
			frappe.db.set_value("Sales Order", sales_order.name, ORDER_STATUS_FIELD, order_status)

	except Exception as e:
		create_shopify_log(status="Error", exception=e, shopify_account=shopify_account_name)
	else:
		create_shopify_log(status="Success", shopify_account=shopify_account_name)


def sync_old_orders():
	all_accounts = frappe.get_all(
		ACCOUNT_DOCTYPE,
		filters={"enable_shopify": 1, "sync_old_orders": 1},
		pluck="name",
	)
	for account in all_accounts:
		shopify_setting = frappe.get_doc(ACCOUNT_DOCTYPE, account)
		if not cint(shopify_setting.sync_old_orders):
			continue

		with get_temp_session_context(shopify_setting):
			orders = _fetch_old_orders(shopify_setting.old_orders_from, shopify_setting.old_orders_to)
			for order in orders:
				log = create_shopify_log(
					method=EVENT_MAPPER["orders/create"], request_data=json.dumps(order), make_new=True, shopify_account=shopify_setting.name
				)
				sync_sales_order(order, request_id=log.name, setting=shopify_setting)

		shopify_setting.sync_old_orders = 0
		shopify_setting.save()


def _fetch_old_orders(from_time, to_time):
	"""Fetch all shopify orders in specified range and return an iterator on fetched orders."""

	from_time = get_datetime(from_time).astimezone().isoformat()
	to_time = get_datetime(to_time).astimezone().isoformat()
	orders_iterator = PaginatedIterator(
		Order.find(created_at_min=from_time, created_at_max=to_time, limit=250)
	)

	for orders in orders_iterator:
		for order in orders:
			# Using generator instead of fetching all at once is better for
			# avoiding rate limits and reducing resource usage.
			yield order.to_dict()
