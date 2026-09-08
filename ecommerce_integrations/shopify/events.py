"""Extension point for other apps: events of a shop order, raised by this connector after it did
its own work. Nothing in this app knows who listens. Handlers register in their hooks.py::

    shopify_order_events = {"order_created": ["my_app.module.on_order_created"]}

and are called synchronously as ``handler(event, sales_order, payload, shopify_account)`` inside
the job that processed the webhook or the live pull - so a failing handler fails that job's log
entry, and Shopify retries the webhook.

Events:
- ``order_created``: the sales order was created and submitted (payload: the shop order).
- ``financial_status_changed``: the connector wrote a new ``shopify_financial_status`` on the
  sales order (payload: the shop order; ``financial_status`` holds the new value).
- ``refund_created``: a ``refunds/create`` webhook for a submitted sales order (payload: the refund).
"""

import frappe

HOOK = "shopify_order_events"

ORDER_CREATED = "order_created"
FINANCIAL_STATUS_CHANGED = "financial_status_changed"
REFUND_CREATED = "refund_created"


def emit(event, sales_order, payload, shopify_account=None):
	"""Call every registered handler for the event; returns their results in registration order."""
	account = shopify_account.name if hasattr(shopify_account, "name") else shopify_account
	results = []
	for path in (frappe.get_hooks(HOOK) or {}).get(event, []):
		results.append(frappe.get_attr(path)(event, sales_order, payload, account))
	return results
