"""Local copies of the personalization files a marketplace order references - the buyer's
photos, the cart previews and the personalizer's per-side SVG renders - plus the font facts
those renders carry.

The line-item properties keep only CDN links (Shopify uploads of the Product Personalizer).
The link stays the truth in the JSON; the copy is what the card and, later, production rely
on once the CDN forgets the file. PII concept (docs/plans/2026-08-27-pii-datenhaltung-konzept.md):
images are class K0 - files with a deletion clock, 30 days after shipment, 90 days without one
(R1, R3) - so the copies live in a raw store outside `private/files` (no backup collects them),
one directory per order with a `meta.json` sidecar, served through one permission-checked
endpoint and purged by a daily job. Same layout as the Amazon app's raw store.

Fonts: the shop hands the font over as a personalizer index ("Schriftart wählen: #22"); only
the SVG render names the family and links the TTF. Both are recorded per order in the sidecar,
the TTF is stored once per account (fonts are no PII, no clock), and the index -> family pairs
accumulate in `font_map.json` per account - the channel key plus resolved family the freight
contract asks for (docs/plans/2026-08-31-b2c-fracht-schnittstelle.md §3 ①).

Nothing here may fail an order import: every entry point catches, logs and reports.

Stage 3 - the reference (plan docs/plans/2026-09-07-b2c-perso-referenz-stufe3.md, user decision
2026-09-07): consumers never copy. The identity of a file is (sales order, row, property); the
`serve` endpoint hands it out to a session or API token with read permission on the order, or to
anyone holding a valid signed URL from `sign` / `references`. Preview signatures live minutes,
production references until the K0 clock of the file (`b2c_perso_purge_after`) - the reference
dies with the file, never later. Every delivery lands in Frappe's Access Log (PII rule R5); a
purged file answers 410 Gone.
"""

import hashlib
import hmac
import json
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, get_url, getdate, now_datetime, today

from ecommerce_integrations.shopify.constants import ORDER_ACCOUNT_FIELD, ORDER_ITEM_PROPERTIES_FIELD

STATE_FIELD = "b2c_perso_files"
PURGE_FIELD = "b2c_perso_purge_after"
STATE_PENDING = "Ausstehend"
STATE_FETCHED = "Geholt"
STATE_FAILED = "Fehlgeschlagen"
STATE_PURGED = "Gelöscht"

META = "meta.json"
FONT_MAP = "font_map.json"
MAX_BYTES = 25 * 1024 * 1024
TIMEOUT = 20
SHIPPED_TTL_DAYS = 30  # K0: 30 days after shipment
UNSHIPPED_TTL_DAYS = 90  # K0: 90 days without one
RETRY_MAX_AGE_DAYS = 7
USER_AGENT = "adomio-b2c-erpnext/1.0 (personalization files)"

# Same rule as the card script: only http(s) URLs with an image extension are files.
IMAGE_URL = re.compile(r"^https?://[^\s\"'<>]+\.(?:jpe?g|png|webp|gif|svg)(?:[?#][^\s\"'<>]*)?$", re.I)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FONT_FACE = re.compile(r"@font-face\s*\{([^}]*)\}", re.I | re.S)
FONT_FAMILY = re.compile(r"font-family\s*:\s*['\"]?([^;'\"}]+)", re.I)
FONT_SRC = re.compile(r"url\(\s*['\"]?([^)'\"]+)", re.I)
USED_FAMILY = re.compile(r"<text[^>]*\sfont-family=\"([^\"]+)\"", re.I)
FONT_KEY = re.compile(r"^#\d{1,4}$")  # personalizer index, e.g. "#22"


class PersonalizationFileError(Exception):
	pass


class FileGone(frappe.ValidationError):
	"""The K0 clock ran out: the copy is purged, the reference is dead."""

	http_status_code = 410


SERVE_METHOD = "ecommerce_integrations.b2c.personalization_files.serve"
PREVIEW_TTL_SECONDS = 15 * 60
PREVIEW_MAX_TTL_SECONDS = 60 * 60
PURPOSES = ("preview", "production")


# --- pure helpers ----------------------------------------------------------------------------


def is_image_url(value) -> bool:
	return bool(IMAGE_URL.match(str(value or "").strip()))


def slug(value, default="bild") -> str:
	folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
	return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:40] or default


def safe_id(value) -> str:
	value = str(value or "").strip()
	if not SAFE_ID.match(value) or ".." in value:
		raise PersonalizationFileError(f"unsafe identifier {value!r}")
	return value


def account_key(account_name) -> str:
	"""ASCII-only directory name for the account (umlauts folded, rest scrubbed)."""
	folded = unicodedata.normalize("NFKD", str(account_name or "")).encode("ascii", "ignore").decode()
	return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_") or "account"


def sniff(data: bytes):
	"""(extension, content type) from the magic bytes, None when it is not an image we keep.
	The URL's extension is not trusted - the CDN answers what it answers."""
	head = data[:16]
	if head.startswith(b"\xff\xd8\xff"):
		return "jpg", "image/jpeg"
	if head.startswith(b"\x89PNG\r\n\x1a\n"):
		return "png", "image/png"
	if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
		return "gif", "image/gif"
	if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
		return "webp", "image/webp"
	text = data[:1024].lstrip(b"\xef\xbb\xbf").lstrip()
	if (text.startswith(b"<?xml") or text.startswith(b"<svg")) and b"<svg" in data[:4096]:
		return "svg", "image/svg+xml"
	return None


def is_font_file(data: bytes) -> bool:
	return data[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO", b"wOFF", b"wOF2")


def plan_targets(rows):
	"""Every image-URL property of every line: [{row, idx, property, url, hidden}]. `rows` are
	Sales Order Item docs or dicts carrying name, idx and the properties JSON."""
	out = []
	for row in rows:
		raw = row.get(ORDER_ITEM_PROPERTIES_FIELD)
		if not raw:
			continue
		try:
			props = json.loads(raw)
		except (TypeError, ValueError):
			continue
		if not isinstance(props, list):
			continue
		for prop in props:
			name = str((prop or {}).get("name") or "").strip()
			value = str((prop or {}).get("value") or "").strip()
			if name and is_image_url(value):
				out.append(
					{
						"row": row.get("name"),
						"idx": cint(row.get("idx")),
						"property": name,
						"url": value,
						"hidden": name.startswith("_"),
					}
				)
	return out


def font_key_of(rows_props) -> str | None:
	"""The personalizer's font index on a line ("Schriftart wählen": "#22"), None when the shop
	names the font directly (then the value already is the family)."""
	for prop in rows_props or []:
		name = str((prop or {}).get("name") or "").lower()
		value = str((prop or {}).get("value") or "").strip()
		if "schrift" in name and FONT_KEY.match(value):
			return value
	return None


def parse_font_faces(svg_text: str):
	faces = []
	for block in FONT_FACE.findall(svg_text or ""):
		family = FONT_FAMILY.search(block)
		src = FONT_SRC.search(block)
		if family:
			faces.append({"family": family.group(1).strip(), "url": src.group(1).strip() if src else None})
	return faces


def used_families(svg_text: str):
	return sorted(set(USED_FAMILY.findall(svg_text or "")))


def http_fetch(url: str) -> bytes:
	import requests

	with requests.get(url, timeout=TIMEOUT, stream=True, headers={"User-Agent": USER_AGENT}) as response:
		if response.status_code != 200:
			raise PersonalizationFileError(f"HTTP {response.status_code}")
		chunks, size = [], 0
		for chunk in response.iter_content(65536):
			size += len(chunk)
			if size > MAX_BYTES:
				raise PersonalizationFileError(f"larger than {MAX_BYTES} bytes")
			chunks.append(chunk)
	return b"".join(chunks)


def fetch_targets(targets, dest, fetch=None, now=None):
	"""Download every target into `dest`; one entry per target, failures recorded, never raised."""
	fetch = fetch or http_fetch
	dest = Path(dest)
	dest.mkdir(parents=True, exist_ok=True)
	stamp = (now or now_datetime()).isoformat()
	entries = []
	for i, target in enumerate(targets):
		entry = {
			**target,
			"file": None,
			"bytes": 0,
			"sha256": None,
			"content_type": None,
			"error": None,
			"fetched_at": stamp,
		}
		try:
			data = fetch(target["url"])
			kind = sniff(data)
			if not kind:
				raise PersonalizationFileError("not an image (magic bytes)")
			ext, content_type = kind
			filename = f"{target.get('idx') or 0:02d}-{slug(str(target['property']).lstrip('_'))}-{i:02d}.{ext}"
			(dest / filename).write_bytes(data)
			entry.update(
				file=filename, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), content_type=content_type
			)
		except Exception as exc:
			entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
		entries.append(entry)
	return entries


def store_font(family, url, fonts_dir, fetch=None):
	"""Keep the TTF once per account; returns the file name or None (font already there counts)."""
	fonts_dir = Path(fonts_dir)
	fonts_dir.mkdir(parents=True, exist_ok=True)
	filename = f"{slug(family, 'font')}.ttf"
	path = fonts_dir / filename
	if path.exists():
		return filename
	if not url or not str(url).lower().startswith(("http://", "https://")):
		return None
	data = (fetch or http_fetch)(url)
	if not is_font_file(data):
		raise PersonalizationFileError("not a font file (magic bytes)")
	path.write_bytes(data)
	return filename


def collect_fonts(entries, dest, rows, fonts_dir, fetch=None):
	"""Per line: the font key from the properties and the family the SVG renders actually use.
	Returns [{row, key, family, url, ttf, error}] - one per (row, family)."""
	dest = Path(dest)
	by_row = {}
	for entry in entries:
		if entry.get("content_type") != "image/svg+xml" or not entry.get("file"):
			continue
		try:
			svg = (dest / entry["file"]).read_text(encoding="utf-8", errors="ignore")
		except OSError:
			continue
		row = by_row.setdefault(entry["row"], {"faces": {}, "used": set()})
		for face in parse_font_faces(svg):
			row["faces"].setdefault(face["family"], face["url"])
		row["used"].update(used_families(svg))

	keys = {}
	for r in rows:
		try:
			props = json.loads(r.get(ORDER_ITEM_PROPERTIES_FIELD) or "[]")
		except (TypeError, ValueError):
			props = []
		keys[r.get("name")] = font_key_of(props)

	fonts = []
	for row_name, found in by_row.items():
		families = sorted(found["used"]) or sorted(found["faces"])
		for family in families:
			url = found["faces"].get(family)
			record = {"row": row_name, "key": keys.get(row_name), "family": family, "url": url, "ttf": None, "error": None}
			try:
				record["ttf"] = store_font(family, url, fonts_dir, fetch=fetch)
			except Exception as exc:
				record["error"] = f"{type(exc).__name__}: {exc}"[:300]
			fonts.append(record)
	return fonts


def update_font_map(path, key, family, ttf, sales_order, now):
	"""Learn the index -> family pair per account; a second family for the same key is kept as a
	conflict, never silently overwritten."""
	path = Path(path)
	data = {}
	if path.exists():
		try:
			data = json.loads(path.read_text(encoding="utf-8"))
		except ValueError:
			data = {}
	entry = data.get(key) or {"family": family, "ttf": ttf, "first_seen": now, "orders": 0, "conflicts": []}
	if entry["family"] != family and family not in entry["conflicts"]:
		entry["conflicts"].append(family)
	entry["ttf"] = entry.get("ttf") or ttf
	entry["last_seen"] = now
	entry["last_order"] = sales_order
	entry["orders"] = cint(entry.get("orders")) + 1
	data[key] = entry
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
	return entry


# --- references and signatures ------------------------------------------------------------------


def signing_key(secret=None) -> bytes:
	"""Derived from the site's encryption key so no second secret has to be managed."""
	if secret is None:
		secret = frappe.local.conf.get("encryption_key") or ""
	if not secret:
		raise PersonalizationFileError("site has no encryption_key")
	return hashlib.sha256(f"{secret}:b2c-perso-reference".encode()).digest()


def make_signature(so, row, property, exp, key=None) -> str:
	message = "|".join([str(so), str(row), str(property), str(int(exp))]).encode()
	return hmac.new(key or signing_key(), message, hashlib.sha256).hexdigest()


def verify_signature(so, row, property, exp, sig, key=None, now=None) -> bool:
	try:
		exp = int(exp)
	except (TypeError, ValueError):
		return False
	now_ts = int((now or datetime.now(timezone.utc)).timestamp())
	if exp <= now_ts:
		return False
	return hmac.compare_digest(make_signature(so, row, property, exp, key=key), str(sig or ""))


def reference_url(so, row, property, exp=None, sig=None, base_url=None) -> str:
	params = {"so": so, "row": row, "property": property}
	if exp is not None and sig:
		params.update(exp=int(exp), sig=sig)
	return f"{base_url or get_url()}/api/method/{SERVE_METHOD}?{urlencode(params)}"


def expiry_for(purpose, purge_after=None, ttl=None, now=None) -> int:
	"""Unix time a signature ends. Preview: minutes (bounded). Production: the end of the file's
	K0 day - the reference must not outlive the copy."""
	now = now or datetime.now(timezone.utc)
	if purpose == "production":
		if not purge_after:
			raise PersonalizationFileError("no K0 clock on the order - nothing to reference")
		day = getdate(purge_after)
		end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)
		return int(end.timestamp())
	seconds = min(cint(ttl) or PREVIEW_TTL_SECONDS, PREVIEW_MAX_TTL_SECONDS)
	return int(now.timestamp()) + max(seconds, 60)


# --- store layout ------------------------------------------------------------------------------


def default_base_dir() -> Path:
	"""Absolute path of the store (frappe.get_site_path is relative to the sites directory, which
	only holds inside bench/scheduler processes)."""
	base = Path(frappe.get_site_path("private", "b2c"))
	if not base.is_absolute():
		from frappe.utils import get_bench_path

		base = Path(get_bench_path()) / "sites" / frappe.local.site / "private" / "b2c"
	return base


def order_dir(account, sales_order, base_dir=None) -> Path:
	return Path(base_dir or default_base_dir()) / "perso" / account_key(account) / safe_id(sales_order)


def fonts_dir(account, base_dir=None) -> Path:
	return Path(base_dir or default_base_dir()) / "fonts" / account_key(account)


def read_meta(account, sales_order, base_dir=None):
	path = order_dir(account, sales_order, base_dir) / META
	if not path.exists():
		return None
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except ValueError:
		return None


# --- entry points (DB-bound) --------------------------------------------------------------------


def _account_of(so) -> str:
	return so.get(ORDER_ACCOUNT_FIELD) or "other"


def fetch_for_sales_order(sales_order, fetch=None, base_dir=None, now=None, only_if_pending=False):
	"""Download the order's personalization files into the store and record the result on the
	order. Never raises - an import must not depend on a CDN."""
	from ecommerce_integrations.b2c.gates import log_gate

	so = frappe.get_doc("Sales Order", sales_order)
	if only_if_pending and so.get(STATE_FIELD) in (STATE_FETCHED, STATE_PURGED):
		return {"sales_order": so.name, "skipped": so.get(STATE_FIELD)}
	targets = plan_targets(so.items)
	if not targets:
		return {"sales_order": so.name, "files": 0}
	now = now or now_datetime()
	account = _account_of(so)
	try:
		dest = order_dir(account, so.name, base_dir)
		entries = fetch_targets(targets, dest, fetch=fetch, now=now)
		fonts = collect_fonts(entries, dest, so.items, fonts_dir(account, base_dir), fetch=fetch)
		for font in fonts:
			if font.get("key") and font.get("family"):
				update_font_map(
					fonts_dir(account, base_dir) / FONT_MAP, font["key"], font["family"], font.get("ttf"), so.name, now.isoformat()
				)
		meta = {
			"sales_order": so.name,
			"account": account,
			"fetched_at": now.isoformat(),
			"files": entries,
			"fonts": fonts,
		}
		(dest / META).write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")

		failed = [e for e in entries if e["error"]]
		values = {STATE_FIELD: STATE_FAILED if failed else STATE_FETCHED}
		if not so.get(PURGE_FIELD):
			values[PURGE_FIELD] = add_days(getdate(so.creation) or getdate(now), UNSHIPPED_TTL_DAYS)
		so.db_set(values, update_modified=False)

		text = f"Personalisierungsdateien: {len(entries) - len(failed)} von {len(entries)} geholt"
		if failed:
			text += "; fehlgeschlagen: " + ", ".join(f"{e['property']} ({e['error']})" for e in failed)
		learned = [f for f in fonts if f.get("family")]
		if learned:
			text += "; Schrift: " + ", ".join(f"{f.get('key') or 'direkt'} = {f['family']}" for f in learned)
		log_gate(so, text)
		return {"sales_order": so.name, "files": len(entries), "failed": len(failed), "fonts": len(learned), "state": values[STATE_FIELD]}
	except Exception:
		frappe.log_error(title=f"B2C personalization files {so.name}", message=frappe.get_traceback())
		try:
			so.db_set(STATE_FIELD, STATE_FAILED, update_modified=False)
			log_gate(so, "Personalisierungsdateien: Abruf fehlgeschlagen (siehe Error Log)")
		except Exception:
			pass
		return {"sales_order": so.name, "error": True}


def after_import(sales_order):
	"""Hook for the order import: fetch once, swallow everything."""
	try:
		return fetch_for_sales_order(sales_order, only_if_pending=True)
	except Exception:
		frappe.log_error(title=f"B2C personalization files {sales_order}", message=frappe.get_traceback())
		return {"sales_order": sales_order, "error": True}


def start_purge_clock(so, now=None):
	"""Shipment signal (K0, R3): the copies go 30 days after shipping. Called from mark_shipped."""
	if so.get(STATE_FIELD) == STATE_PURGED:
		return None
	purge_after = add_days(getdate(now) if now else getdate(today()), SHIPPED_TTL_DAYS)
	so.db_set(PURGE_FIELD, purge_after, update_modified=False)
	return purge_after


def retry_pending(now=None, fetch=None):
	"""Hourly: orders whose download failed get another try for a week."""
	now = now or now_datetime()
	names = frappe.get_all(
		"Sales Order",
		filters={
			STATE_FIELD: ["in", [STATE_PENDING, STATE_FAILED]],
			"docstatus": 1,
			"creation": [">", add_days(now, -RETRY_MAX_AGE_DAYS)],
		},
		pluck="name",
	)
	results = [fetch_for_sales_order(name, fetch=fetch, now=now) for name in names]
	frappe.db.commit()
	return results


def backfill(limit=50, fetch=None):
	"""Orders imported before this module existed: fetch where the properties carry image URLs
	and no state is recorded yet."""
	rows = frappe.db.sql(
		"""select distinct so.name from `tabSales Order` so
		join `tabSales Order Item` soi on soi.parent = so.name
		where so.docstatus = 1 and ifnull(so.{state}, '') = ''
		and soi.{props} like '%%http%%' order by so.creation desc limit %s""".format(
			state=STATE_FIELD, props=ORDER_ITEM_PROPERTIES_FIELD
		),
		(cint(limit),),
		pluck="name",
	)
	results = [fetch_for_sales_order(name, fetch=fetch) for name in rows]
	frappe.db.commit()
	return results


def purge_expired(now=None, base_dir=None):
	"""Daily: delete order directories whose clock ran out, stamp the state, sweep orphans
	(directories of orders that no longer exist - a rolled-back import leaves one behind)."""
	from ecommerce_integrations.b2c.gates import log_gate

	now = now or now_datetime()
	purged, orphans, errors = [], [], []
	rows = frappe.get_all(
		"Sales Order",
		filters={STATE_FIELD: ["in", [STATE_FETCHED, STATE_FAILED, STATE_PENDING]], PURGE_FIELD: ["<=", getdate(now)]},
		fields=["name", ORDER_ACCOUNT_FIELD],
	)
	for row in rows:
		try:
			path = order_dir(row.get(ORDER_ACCOUNT_FIELD) or "other", row.name, base_dir)
			if path.exists():
				shutil.rmtree(path)
			frappe.db.set_value("Sales Order", row.name, STATE_FIELD, STATE_PURGED, update_modified=False)
			log_gate(frappe.get_doc("Sales Order", row.name), "Personalisierungsdateien gelöscht (K0-Frist)")
			purged.append(row.name)
		except Exception:
			frappe.log_error(title=f"B2C personalization purge {row.name}", message=frappe.get_traceback())
			errors.append(row.name)
	base = Path(base_dir or default_base_dir()) / "perso"
	if base.exists():
		for account_path in base.iterdir():
			if not account_path.is_dir():
				continue
			for so_path in account_path.iterdir():
				if so_path.is_dir() and not frappe.db.exists("Sales Order", so_path.name):
					shutil.rmtree(so_path, ignore_errors=True)
					orphans.append(so_path.name)
	frappe.db.commit()
	return {"purged": purged, "orphans": orphans, "errors": errors}


# --- HTTP ------------------------------------------------------------------------------------


def _check_read(sales_order):
	if not frappe.has_permission("Sales Order", "read", sales_order):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _log_access(so, property, method, how):
	"""Every delivery of a personalization file is a PII access (R5). `how` names the door:
	session, token or signature."""
	from frappe.core.doctype.access_log.access_log import make_access_log

	try:
		make_access_log(doctype="Sales Order", document=so, method=method, page=property, filters={"via": how})
	except Exception:
		frappe.log_error(title=f"B2C personalization access log {so}", message=frappe.get_traceback())


def _order_files(so):
	state, purge_after, account = frappe.db.get_value("Sales Order", so, [STATE_FIELD, PURGE_FIELD, ORDER_ACCOUNT_FIELD])
	if state == STATE_PURGED:
		frappe.throw(_("The personalization files of this order were deleted after their retention period"), FileGone)
	meta = read_meta(account or "other", so) if state in (STATE_FETCHED, STATE_FAILED) else None
	return state, purge_after, account or "other", meta


@frappe.whitelist()
def sign(so, row, property, purpose="preview", ttl=None):
	"""A signed reference for one file. Needs read permission on the order; the URL itself
	then works without a login until it expires."""
	_check_read(so)
	if purpose not in PURPOSES:
		frappe.throw(_("Unknown purpose"))
	state, purge_after, account, meta = _order_files(so)
	entry = next((e for e in (meta or {}).get("files", []) if e.get("row") == row and e.get("property") == property and e.get("file")), None)
	if not entry:
		frappe.throw(_("File not found"), frappe.DoesNotExistError)
	exp = expiry_for(purpose, purge_after=purge_after, ttl=ttl)
	sig = make_signature(so, row, property, exp)
	return {"url": reference_url(so, row, property, exp, sig), "exp": exp, "purpose": purpose}


@frappe.whitelist()
def references(so, purpose="production"):
	"""Signed references for every file of an order - what a consumer (the Oro connector) asks
	for once per order instead of copying anything."""
	_check_read(so)
	if purpose not in PURPOSES:
		frappe.throw(_("Unknown purpose"))
	state, purge_after, account, meta = _order_files(so)
	files = [e for e in (meta or {}).get("files", []) if e.get("file")]
	out = {"sales_order": so, "state": state, "purge_after": purge_after, "purpose": purpose, "files": []}
	if not files:
		return out
	exp = expiry_for(purpose, purge_after=purge_after)
	for e in files:
		sig = make_signature(so, e["row"], e["property"], exp)
		out["files"].append(
			{
				"row": e["row"],
				"property": e["property"],
				"hidden": bool(e.get("hidden")),
				"content_type": e.get("content_type"),
				"source": e.get("url"),
				"url": reference_url(so, e["row"], e["property"], exp, sig),
				"exp": exp,
			}
		)
	return out


@frappe.whitelist()
def info(so):
	"""What the card needs: state, clock, which files are there (with their unsigned reference),
	the learned fonts."""
	_check_read(so)
	state, purge_after, account = frappe.db.get_value("Sales Order", so, [STATE_FIELD, PURGE_FIELD, ORDER_ACCOUNT_FIELD])
	meta = read_meta(account or "other", so) if state in (STATE_FETCHED, STATE_FAILED) else None
	files = [
		{
			"row": e.get("row"),
			"property": e.get("property"),
			"ok": bool(e.get("file")),
			"bytes": e.get("bytes"),
			"error": e.get("error"),
			"reference": reference_url(so, e.get("row"), e.get("property")) if e.get("file") else None,
		}
		for e in (meta or {}).get("files", [])
	]
	fonts = [
		{"row": f.get("row"), "key": f.get("key"), "family": f.get("family"), "ttf": bool(f.get("ttf"))}
		for f in (meta or {}).get("fonts", [])
	]
	return {"state": state, "purge_after": purge_after, "files": files, "fonts": fonts}


@frappe.whitelist(allow_guest=True)
def serve(so, row, property, exp=None, sig=None):
	"""The local copy of one property, inline. Two doors: a session or API token with read
	permission on the order, or a valid signed URL (see `sign` / `references`). Guests without a
	valid signature are refused before anything is read."""
	from werkzeug.wrappers import Response

	if sig:
		if not verify_signature(so, row, property, exp, sig):
			frappe.throw(_("Signature invalid or expired"), frappe.PermissionError)
		how = "signature"
	else:
		if frappe.session.user == "Guest":
			frappe.throw(_("Not permitted"), frappe.PermissionError)
		_check_read(so)
		how = "token" if frappe.get_request_header("Authorization") else "session"
	state, _purge_after, account, meta = _order_files(so)
	entry = next(
		(e for e in (meta or {}).get("files", []) if e.get("row") == row and e.get("property") == property and e.get("file")),
		None,
	)
	if not entry:
		frappe.throw(_("File not found"), frappe.DoesNotExistError)
	directory = order_dir(account, so).resolve()
	path = (directory / entry["file"]).resolve()
	if directory not in path.parents or not path.is_file():
		frappe.throw(_("File not found"), frappe.DoesNotExistError)
	_log_access(so, property, SERVE_METHOD, how)
	response = Response(path.read_bytes(), mimetype=entry.get("content_type") or "application/octet-stream")
	response.headers["Content-Disposition"] = f'inline; filename="{entry["file"]}"'
	response.headers["Cache-Control"] = "private, max-age=3600"
	response.headers["X-Content-Type-Options"] = "nosniff"
	if entry.get("content_type") == "image/svg+xml":
		# The render is shown through <img>; the sandbox keeps a script inside the SVG inert
		# should someone open the URL directly.
		response.headers["Content-Security-Policy"] = "sandbox"
	return response
