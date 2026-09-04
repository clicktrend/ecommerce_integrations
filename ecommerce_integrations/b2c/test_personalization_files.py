"""Pure tests for the personalization raw store: URL rule, magic bytes, targets, fonts, store
writes with a fake fetcher. No site, no DB (the Shopify test base is dead - README §8)."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ecommerce_integrations.b2c import personalization_files as pf

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
TTF = b"\x00\x01\x00\x00" + b"\x00" * 64
SVG = (
	'<svg xmlns="http://www.w3.org/2000/svg" width="4in" height="4in"><defs><style>'
	"@font-face {font-family: 'Allan-regular';src: url(https://cdn-zeptoapps.com/product-personalizer/font/shop/Allan-regular.ttf?v=1) format('truetype');}"
	'</style></defs><g><text fill="rgb(0,0,0)" font-family="Allan-regular" font-size="36pt" x="1" y="2">Name</text></g></svg>'
)
PROPS = json.dumps(
	[
		{"name": "Textgravur Rückseite", "value": "Name\r\nAlexandros"},
		{"name": "Schriftart wählen", "value": "#22"},
		{"name": "Bild Vorderseite", "value": "https://cdn.shopify.com/s/files/1/x/uploads/a.jpeg"},
		{"name": "_svg 1", "value": "https://cdn.shopify.com/s/files/1/x/uploads/b.svg"},
		{"name": "_pplr_preview", "value": "Vorschau"},
		{"name": "Kaputt", "value": "https://cdn.shopify.com/s/files/1/x/uploads/c.png"},
	]
)
ROWS = [
	{"name": "row1", "idx": 1, "shopify_item_properties": PROPS},
	{"name": "row2", "idx": 2, "shopify_item_properties": None},
]


def make_fetch(log=None):
	def fetch(url):
		if log is not None:
			log.append(url)
		if url.endswith("a.jpeg"):
			return JPEG
		if url.endswith("b.svg"):
			return SVG.encode()
		if url.endswith("Allan-regular.ttf?v=1"):
			return TTF
		raise pf.PersonalizationFileError("HTTP 404")

	return fetch


class TestImageUrl(unittest.TestCase):
	def test_images(self):
		for url in (
			"https://cdn.shopify.com/s/files/1/0301/uploads/x.jpeg",
			"https://cdn.shopify.com/x/y.PNG",
			"https://cdn.shopify.com/x/y.svg?v=12#a",
			"http://example.com/a.webp",
		):
			self.assertTrue(pf.is_image_url(url), url)

	def test_not_images(self):
		for value in ("Vorschau", "https://example.com/page", "javascript:alert(1).png", "ftp://x/y.jpg", "", None):
			self.assertFalse(pf.is_image_url(value), value)


class TestSniff(unittest.TestCase):
	def test_kinds(self):
		self.assertEqual(pf.sniff(JPEG), ("jpg", "image/jpeg"))
		self.assertEqual(pf.sniff(PNG), ("png", "image/png"))
		self.assertEqual(pf.sniff(WEBP), ("webp", "image/webp"))
		self.assertEqual(pf.sniff(SVG.encode()), ("svg", "image/svg+xml"))
		self.assertEqual(pf.sniff(b"\xef\xbb\xbf<?xml version='1.0'?><svg/>"), ("svg", "image/svg+xml"))

	def test_rejects(self):
		self.assertIsNone(pf.sniff(b"<html><body>404</body></html>"))
		self.assertIsNone(pf.sniff(b""))
		self.assertTrue(pf.is_font_file(TTF))
		self.assertFalse(pf.is_font_file(JPEG))


class TestNames(unittest.TestCase):
	def test_slug(self):
		self.assertEqual(pf.slug("Bild Vorderseite"), "bild-vorderseite")
		self.assertEqual(pf.slug("Bild Rückseite_crop"), "bild-ruckseite-crop")
		self.assertEqual(pf.slug(""), "bild")
		self.assertEqual(pf.account_key("mit-bildgravur-de.myshopify.com"), "mit_bildgravur_de_myshopify_com")

	def test_safe_id(self):
		self.assertEqual(pf.safe_id("SO-SHP-2026-00029"), "SO-SHP-2026-00029")
		with self.assertRaises(pf.PersonalizationFileError):
			pf.safe_id("../etc")


class TestTargets(unittest.TestCase):
	def test_plan(self):
		targets = pf.plan_targets(ROWS)
		self.assertEqual([t["property"] for t in targets], ["Bild Vorderseite", "_svg 1", "Kaputt"])
		self.assertEqual([t["hidden"] for t in targets], [False, True, False])
		self.assertTrue(all(t["row"] == "row1" and t["idx"] == 1 for t in targets))

	def test_font_key(self):
		self.assertEqual(pf.font_key_of(json.loads(PROPS)), "#22")
		self.assertIsNone(pf.font_key_of([{"name": "Schriftart", "value": "Anna & Ben 5"}]))
		self.assertIsNone(pf.font_key_of([]))


class TestFontParsing(unittest.TestCase):
	def test_faces_and_usage(self):
		faces = pf.parse_font_faces(SVG)
		self.assertEqual(faces[0]["family"], "Allan-regular")
		self.assertTrue(faces[0]["url"].startswith("https://cdn-zeptoapps.com/"))
		self.assertEqual(pf.used_families(SVG), ["Allan-regular"])
		self.assertEqual(pf.parse_font_faces("<svg/>"), [])


class TestStore(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.base = Path(self.tmp.name)
		self.now = datetime(2026, 9, 4, 16, 0, 0)

	def tearDown(self):
		self.tmp.cleanup()

	def test_fetch_targets_records_success_and_failure(self):
		dest = self.base / "perso" / "acc" / "SO-1"
		entries = pf.fetch_targets(pf.plan_targets(ROWS), dest, fetch=make_fetch(), now=self.now)
		self.assertEqual(len(entries), 3)
		ok = [e for e in entries if e["file"]]
		self.assertEqual([e["file"] for e in ok], ["01-bild-vorderseite-00.jpg", "01-svg-1-01.svg"])
		self.assertEqual([e["content_type"] for e in ok], ["image/jpeg", "image/svg+xml"])
		self.assertTrue(all((dest / e["file"]).exists() and e["sha256"] and e["bytes"] for e in ok))
		failed = [e for e in entries if e["error"]]
		self.assertEqual(failed[0]["property"], "Kaputt")
		self.assertIn("HTTP 404", failed[0]["error"])

	def test_wrong_content_is_rejected(self):
		entries = pf.fetch_targets(
			[{"row": "r", "idx": 1, "property": "Bild", "url": "https://x/y.jpg", "hidden": False}],
			self.base / "d",
			fetch=lambda url: b"<html>not found</html>",
			now=self.now,
		)
		self.assertIsNone(entries[0]["file"])
		self.assertIn("magic bytes", entries[0]["error"])

	def test_fonts_learned_and_ttf_stored_once(self):
		dest = self.base / "perso" / "acc" / "SO-1"
		log = []
		fetch = make_fetch(log)
		entries = pf.fetch_targets(pf.plan_targets(ROWS), dest, fetch=fetch, now=self.now)
		fonts = pf.collect_fonts(entries, dest, ROWS, self.base / "fonts" / "acc", fetch=fetch)
		self.assertEqual(len(fonts), 1)
		self.assertEqual((fonts[0]["row"], fonts[0]["key"], fonts[0]["family"], fonts[0]["ttf"]), ("row1", "#22", "Allan-regular", "allan-regular.ttf"))
		self.assertTrue((self.base / "fonts" / "acc" / "allan-regular.ttf").exists())
		ttf_calls = [u for u in log if u.endswith(".ttf?v=1")]
		self.assertEqual(len(ttf_calls), 1)
		pf.collect_fonts(entries, dest, ROWS, self.base / "fonts" / "acc", fetch=fetch)
		self.assertEqual(len([u for u in log if u.endswith(".ttf?v=1")]), 1)  # already there

	def test_font_map_accumulates_and_keeps_conflicts(self):
		path = self.base / "fonts" / "acc" / pf.FONT_MAP
		pf.update_font_map(path, "#22", "Allan-regular", "allan-regular.ttf", "SO-1", "2026-09-04T16:00:00")
		entry = pf.update_font_map(path, "#22", "Allan-regular", None, "SO-2", "2026-09-05T16:00:00")
		self.assertEqual((entry["orders"], entry["ttf"], entry["last_order"]), (2, "allan-regular.ttf", "SO-2"))
		entry = pf.update_font_map(path, "#22", "Other-font", None, "SO-3", "2026-09-06T16:00:00")
		self.assertEqual(entry["family"], "Allan-regular")
		self.assertEqual(entry["conflicts"], ["Other-font"])
		data = json.loads(path.read_text())
		self.assertEqual(set(data), {"#22"})

	def test_order_dir_layout(self):
		path = pf.order_dir("mit-bildgravur-de.myshopify.com", "SO-SHP-2026-00029", self.base)
		self.assertEqual(path, self.base / "perso" / "mit_bildgravur_de_myshopify_com" / "SO-SHP-2026-00029")
		self.assertEqual(pf.fonts_dir("mit-bildgravur-de.myshopify.com", self.base), self.base / "fonts" / "mit_bildgravur_de_myshopify_com")
