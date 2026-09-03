"""Live pull from the shop for a dev site without a public URL (parity window, 2026-09-03).

Webhooks cannot reach the dev bench, so this reads the shop with the account's token instead:
orders updated since the last cursor are fetched; unknown ones go through the same
sync_sales_order() the webhook would call, known ones get the payment status and a
cancellation applied. It never saves the Shopify Account document - saving with
`enable_shopify = 1` registers webhooks that would point at the dev host (README §1).

Reads only. Payment changes only move the B2C state (no invoice at payment: the invoice is
made at shipping, decision 2026-09-03). Cursor: a global default value per account.

Run periodically (no scheduler on the dev bench):
    bench --site b2c.local execute ecommerce_integrations.b2c.live_pull.pull
    bench --site b2c.local execute ecommerce_integrations.b2c.live_pull.pull --kwargs "{'minutes': 180}"
"""

import json
from datetime import datetime, timedelta, timezone

import frappe
from frappe.utils import cstr, get_datetime

from ecommerce_integrations.shopify.constants import ACCOUNT_DOCTYPE, EVENT_MAPPER, ORDER_ID_FIELD
from ecommerce_integrations.shopify.utils import create_shopify_log

DEFAULT_WINDOW_MINUTES = 60
OVERLAP_SECONDS = 60  # re-read a minute of overlap so a boundary update is never missed


def cursor_key(account_name):
	return f"b2c_live_pull_cursor:{account_name}"


def start_key(account_name):
	return f"b2c_live_pull_start:{account_name}"


def get_window_start(account_name):
	value = frappe.db.get_default(start_key(account_name))
	return as_utc(value) if value else None


def as_utc(value):
	"""Any ISO string / datetime -> aware UTC datetime (naive values are taken as UTC)."""
	if value is None:
		return None
	value = get_datetime(value)
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)


def get_cursor(account_name):
	value = frappe.db.get_default(cursor_key(account_name))
	return as_utc(value) if value else None


def set_cursor(account_name, value):
	frappe.db.set_default(cursor_key(account_name), as_utc(value).isoformat())


def default_account():
	name = frappe.db.get_value(ACCOUNT_DOCTYPE, {"enable_shopify": 1}, "name")
	if not name:
		frappe.throw("No enabled Shopify Account")
	return name


def apply_order(order, setting, window_start=None):
	"""One shop order: sync it, or apply what changed on the known sales order. Orders created
	before the parity window only count as `old`: an update (fulfilment, payout) on an order
	the shop shipped weeks ago must not start a dev production run."""
	from ecommerce_integrations.b2c import gates
	from ecommerce_integrations.shopify.order import cancel_order, sync_sales_order

	order_id = cstr(order.get("id"))
	existing = frappe.db.get_value(
		"Sales Order",
		{ORDER_ID_FIELD: order_id},
		["name", "docstatus", "shopify_financial_status"],
		as_dict=True,
	)
	if not existing:
		created = as_utc(order.get("created_at"))
		if window_start and created and created < window_start:
			return "old"
		log = create_shopify_log(
			method=EVENT_MAPPER["orders/create"],
			request_data=json.dumps(order, default=str),
			make_new=True,
			shopify_account=setting.name,
		)
		sync_sales_order(order, request_id=log.name, shopify_account=setting)
		return "created"

	if existing.docstatus != 1:
		return "skipped"

	if order.get("cancelled_at"):
		log = create_shopify_log(
			method=EVENT_MAPPER["orders/cancelled"],
			request_data=json.dumps(order, default=str),
			make_new=True,
			shopify_account=setting.name,
		)
		cancel_order(order, request_id=log.name, shopify_account=setting)
		return "cancelled"

	status = order.get("financial_status")
	if status and status != existing.shopify_financial_status:
		gates.mark_paid(existing.name, status)
		return "payment"

	return "unchanged"


def pull(account=None, minutes=None, dry_run=False):
	"""Fetch orders updated since the cursor (or the last `minutes`) and apply them."""
	import shopify
	from shopify.collection import PaginatedIterator

	from ecommerce_integrations.shopify.connection import get_temp_session_context

	frappe.set_user("Administrator")
	account = account or default_account()
	setting = frappe.get_doc(ACCOUNT_DOCTYPE, account)
	if not setting.is_enabled():
		return {"account": account, "skipped": "account disabled"}

	since = get_cursor(account)
	if minutes or since is None:
		since = datetime.now(timezone.utc) - timedelta(minutes=int(minutes or DEFAULT_WINDOW_MINUTES))
	# The parity window opens with the first real run; earlier orders are never imported here.
	window_start = get_window_start(account)
	if window_start is None and not dry_run:
		window_start = since
		frappe.db.set_default(start_key(account), window_start.isoformat())
	since = since - timedelta(seconds=OVERLAP_SECONDS)
	since_iso = since.isoformat()

	counts = {}
	newest = since
	with get_temp_session_context(setting):
		pages = PaginatedIterator(
			shopify.Order.find(status="any", updated_at_min=since_iso, order="updated_at asc", limit=250)
		)
		for page in pages:
			for shop_order in page:
				order = shop_order.to_dict()
				updated = as_utc(order.get("updated_at"))
				if updated and updated > newest:
					newest = updated
				if dry_run:
					outcome = "dry_run"
				else:
					try:
						outcome = apply_order(order, setting, window_start)
					except Exception:
						frappe.log_error(title=f"B2C live pull {order.get('name')}", message=frappe.get_traceback())
						outcome = "error"
				counts[outcome] = counts.get(outcome, 0) + 1

	if not dry_run:
		set_cursor(account, newest)
		frappe.db.commit()
	return {
		"account": account,
		"window_start": window_start.isoformat() if window_start else None,
		"since": since.isoformat(),
		"cursor": newest.isoformat(),
		**counts,
	}
