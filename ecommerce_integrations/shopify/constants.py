# Copyright (c) 2021, Frappe and contributors
# For license information, please see LICENSE


MODULE_NAME = "shopify"
SETTING_DOCTYPE = "Shopify Setting"  # Legacy singleton
ACCOUNT_DOCTYPE = "Shopify Account"  # New multi-tenant account
OLD_SETTINGS_DOCTYPE = "Shopify Settings"

API_VERSION = "2024-01"

WEBHOOK_EVENTS = [
	"orders/create",
	"orders/paid",
	"orders/fulfilled",
	"orders/cancelled",
	"orders/partially_fulfilled",
]

EVENT_MAPPER = {
	"orders/create": "ecommerce_integrations.shopify.order.sync_sales_order",
	"orders/paid": "ecommerce_integrations.shopify.invoice.prepare_sales_invoice",
	"orders/fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
	"orders/cancelled": "ecommerce_integrations.shopify.order.cancel_order",
	"orders/partially_fulfilled": "ecommerce_integrations.shopify.fulfillment.prepare_delivery_note",
}

SHOPIFY_VARIANTS_ATTR_LIST = ["option1", "option2", "option3"]

# custom fields

CUSTOMER_ID_FIELD = "shopify_customer_id"
ORDER_ID_FIELD = "shopify_order_id"
ORDER_NUMBER_FIELD = "shopify_order_number"
ORDER_STATUS_FIELD = "shopify_order_status"
# Head facts the freight contract needs (concept 2026-08-31-b2c-fracht-schnittstelle §2).
# The channel matters most: with multi-shop the account IS the channel.
ORDER_ACCOUNT_FIELD = "shopify_account"
ORDER_FINANCIAL_STATUS_FIELD = "shopify_financial_status"
ORDER_PAYMENT_GATEWAY_FIELD = "shopify_payment_gateway"
ORDER_PLACED_AT_FIELD = "shopify_ordered_at"
FULLFILLMENT_ID_FIELD = "shopify_fulfillment_id"
SUPPLIER_ID_FIELD = "shopify_supplier_id"
ADDRESS_ID_FIELD = "shopify_address_id"
ORDER_ITEM_DISCOUNT_FIELD = "shopify_item_discount"
ORDER_ITEM_PROPERTIES_FIELD = "shopify_item_properties"
ITEM_SELLING_RATE_FIELD = "shopify_selling_rate"

# ERPNext already defines the default UOMs from Shopify but names are different
WEIGHT_TO_ERPNEXT_UOM_MAP = {"kg": "Kg", "g": "Gram", "oz": "Ounce", "lb": "Pound"}
