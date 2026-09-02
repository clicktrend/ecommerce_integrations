"""Replay stored Shopify order payloads through the import - dev tooling.

The webhook path is `store_request_data -> process_request -> sync_sales_order(payload)`.
This module feeds the same function from JSON files (one raw Shopify order each, e.g. the
`original_order` an Oro RFO kept), so the whole chain - sales order, server scripts,
purchase order, downstream connector - can be exercised in real time without Shopify
sending anything and without touching its API for orders. The API is contacted only for
products the site has not synced yet, and only when the caller allows it, paced well below
the REST bucket (40 calls, 2/s refill).

    bench --site b2c.local execute ecommerce_integrations.shopify.replay.replay_orders \
        --kwargs "{'directory': 'replay', 'interval': 30, 'limit': 10}"

Runs only on a developer_mode site: it creates real sales orders.
"""

import csv
import json
import os
import time
from pathlib import Path

import frappe
from frappe.utils import cstr, now_datetime

from ecommerce_integrations.shopify.constants import ORDER_ID_FIELD
from ecommerce_integrations.shopify.order import sync_sales_order
from ecommerce_integrations.shopify.product import ShopifyProduct
from ecommerce_integrations.shopify.utils import create_shopify_log

SYNC_METHOD = "ecommerce_integrations.shopify.order.sync_sales_order"


def replay_orders(
	directory: str = "replay",
	shopify_account: str | None = None,
	interval: float = 0,
	limit: int = 0,
	allow_product_fetch: int = 0,
	fetch_pause: float = 1.0,
	dry_run: int = 0,
):
	"""Replay every *.json in `directory` (relative to the sites folder) in file order.

	interval             seconds to wait between orders ("real time" pacing)
	limit                stop after this many replayed orders (0 = all)
	allow_product_fetch  fetch at most this many unsynced products from Shopify, paced by
	                     fetch_pause; orders needing more are skipped, not imported half
	dry_run              only report what would happen
	"""
	# `bench execute` swallows any exception of the called function and falls back to
	# eval(), which reports a misleading NameError - so the traceback is returned instead.
	try:
		return _replay(directory, shopify_account, interval, limit, allow_product_fetch, fetch_pause, dry_run)
	except Exception:
		import traceback

		frappe.db.rollback()
		return {"error": traceback.format_exc()}


def _replay(directory, shopify_account, interval, limit, allow_product_fetch, fetch_pause, dry_run):
	if not frappe.conf.developer_mode:
		frappe.throw("replay_orders creates real sales orders - developer_mode sites only")

	account = _account(shopify_account)
	folder = Path(frappe.get_site_path("..", directory)).resolve() if not os.path.isabs(directory) else Path(directory)
	files = sorted(folder.glob("*.json"))
	if not files:
		return {"directory": str(folder), "files": 0, "note": "nothing to replay"}

	rows = []
	fetch_budget = int(allow_product_fetch)
	replayed = 0

	for path in files:
		if limit and replayed >= limit:
			break

		payload = json.loads(path.read_text(encoding="utf-8"))
		row = {"file": path.name, "shopify_id": cstr(payload.get("id")), "name": payload.get("name")}

		existing = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: row["shopify_id"]}, "name")
		if existing:
			row.update(result="exists", sales_order=existing, purchase_order=_purchase_order_of(existing))
			rows.append(row)
			continue

		unsynced = _unsynced_products(payload, account.company)
		if unsynced:
			if not allow_product_fetch or len(unsynced) > fetch_budget:
				row.update(result="skipped", message=f"{len(unsynced)} unsynced product(s): {', '.join(unsynced)}")
				rows.append(row)
				continue
			if not dry_run:
				for product_id in unsynced:
					ShopifyProduct(product_id, company=account.company).sync_product()
					frappe.db.commit()
					time.sleep(fetch_pause)
			fetch_budget -= len(unsynced)
			row["fetched_products"] = len(unsynced)

		if dry_run:
			row.update(result="would replay")
			rows.append(row)
			replayed += 1
			continue

		# Same log record the webhook path creates, so the replay is indistinguishable in
		# the Ecommerce Integration Log - and its status is the verdict, not the return.
		log = create_shopify_log(method=SYNC_METHOD, request_data=payload, shopify_account=account, make_new=True)
		frappe.flags.request_id = None
		sync_sales_order(payload, request_id=log.name, shopify_account=account)
		frappe.db.commit()

		log.reload()
		sales_order = frappe.db.get_value("Sales Order", {ORDER_ID_FIELD: row["shopify_id"]}, "name")
		purchase_order = _purchase_order_of(sales_order) if sales_order else None
		row.update(
			result=log.status,
			message=(log.message or "")[:200] if log.status != "Success" else "",
			log=log.name,
			sales_order=sales_order,
			purchase_order=purchase_order,
		)
		rows.append(row)
		replayed += 1

		if interval and replayed < (limit or len(files)):
			time.sleep(interval)

	summary = {
		"directory": str(folder),
		"files": len(files),
		"replayed": replayed,
		"success": sum(1 for r in rows if r.get("result") == "Success"),
		"error": sum(1 for r in rows if r.get("result") == "Error"),
		"skipped": sum(1 for r in rows if r.get("result") == "skipped"),
		"exists": sum(1 for r in rows if r.get("result") == "exists"),
		"without_purchase_order": [r["sales_order"] for r in rows if r.get("sales_order") and not r.get("purchase_order")],
		# a "Success" log without a sales order is a defect of the import, not of the order
		"success_without_sales_order": [r["name"] for r in rows if r.get("result") == "Success" and not r.get("sales_order")],
	}
	if not dry_run and rows:
		summary["report"] = _write_report(folder, rows)
	summary["rows"] = rows
	return summary


def _purchase_order_of(sales_order: str) -> str | None:
	return frappe.db.get_value("Purchase Order Item", {"sales_order": sales_order, "docstatus": ["<", 2]}, "parent")


def _account(name: str | None):
	if name:
		return frappe.get_doc("Shopify Account", name)
	accounts = frappe.get_all("Shopify Account", pluck="name")
	if len(accounts) != 1:
		frappe.throw(f"Pass shopify_account, the site has {len(accounts)} accounts")
	return frappe.get_doc("Shopify Account", accounts[0])


def _unsynced_products(payload, company) -> list[str]:
	"""Products the site has never synced. Checked per product, not per variant: a variant
	that no longer exists in the shop would otherwise trigger a fetch on every run, and
	whether a LINE resolves is the import's verdict, not the replay's."""
	seen = []
	for line in payload.get("line_items") or []:
		product_id = cstr(line.get("product_id"))
		if product_id and product_id not in seen and not ShopifyProduct(product_id, company=company).is_synced():
			seen.append(product_id)
	return seen


def _write_report(folder: Path, rows) -> str:
	path = folder / f"replay-{now_datetime().strftime('%Y%m%d-%H%M%S')}.csv"
	fields = ["file", "shopify_id", "name", "result", "sales_order", "purchase_order", "log", "fetched_products", "message"]
	with path.open("w", newline="", encoding="utf-8") as fh:
		writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
		writer.writeheader()
		writer.writerows(rows)
	return str(path)
