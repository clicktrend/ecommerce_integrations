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
ORDER_ITEM_PERSONALIZATION_SECTION = "shopify_personalization_section"
ORDER_ITEM_PERSONALIZED_FIELD = "shopify_personalized"
# Marketing consent as Shopify records it (email_marketing_consent), kept on the Contact.
# A checkbox alone is no evidence of consent; the state, the opt-in level and the time
# it last changed are (GDPR Art. 5(2)). Concept 2026-09-01-b2c-kundenbestand §5.2.
CONTACT_MARKETING_STATE_FIELD = "shopify_email_marketing_state"
CONTACT_MARKETING_OPT_IN_LEVEL_FIELD = "shopify_email_marketing_opt_in_level"
CONTACT_MARKETING_CONSENT_AT_FIELD = "shopify_email_marketing_consent_at"
ITEM_SELLING_RATE_FIELD = "shopify_selling_rate"

# ERPNext already defines the default UOMs from Shopify but names are different
WEIGHT_TO_ERPNEXT_UOM_MAP = {"kg": "Kg", "g": "Gram", "oz": "Ounce", "lb": "Pound"}
