"""``refunds/create`` webhook: resolve the sales order and hand the refund to whoever listens
(shopify.events). What a refund means for the books - a credit note, a parked order - is not the
connector's business; the B2C app books it through the ``refund_created`` event.
"""

import json

import frappe

from ecommerce_integrations.shopify import events
from ecommerce_integrations.shopify.constants import ORDER_ID_FIELD
from ecommerce_integrations.shopify.utils import create_shopify_log


def process_refund(payload, request_id=None, shopify_account=None):
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	refund = json.loads(payload) if isinstance(payload, str) else payload
	if isinstance(shopify_account, str):
		shopify_account = frappe.get_doc("Shopify Account", shopify_account)
	account_name = shopify_account.name if shopify_account else None

	try:
		sales_order = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: refund.get("order_id"), "docstatus": 1})
		if not sales_order:
			create_shopify_log(status="Invalid", message="Sales Order not found for refund", shopify_account=account_name)
			return
		results = events.emit(events.REFUND_CREATED, sales_order, refund, account_name)
	except Exception as e:
		create_shopify_log(status="Error", exception=e, rollback=True, shopify_account=account_name)
	else:
		create_shopify_log(status="Success", message=json.dumps(results, default=str), shopify_account=account_name)
