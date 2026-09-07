"""Stage 3 - the reference contract, pure: signatures, expiry rule, URL shape (2026-09-07)."""

import unittest
from datetime import datetime, timedelta, timezone

from ecommerce_integrations.b2c import personalization_files as pf

KEY = b"k" * 32
NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


class TestSignature(unittest.TestCase):
	def test_round_trip_and_tamper(self):
		exp = int((NOW + timedelta(minutes=15)).timestamp())
		sig = pf.make_signature("SO-1", "row1", "_svg 1", exp, key=KEY)
		self.assertTrue(pf.verify_signature("SO-1", "row1", "_svg 1", exp, sig, key=KEY, now=NOW))
		self.assertFalse(pf.verify_signature("SO-2", "row1", "_svg 1", exp, sig, key=KEY, now=NOW))
		self.assertFalse(pf.verify_signature("SO-1", "row1", "Bild Vorderseite", exp, sig, key=KEY, now=NOW))
		self.assertFalse(pf.verify_signature("SO-1", "row1", "_svg 1", exp + 1, sig, key=KEY, now=NOW))
		self.assertFalse(pf.verify_signature("SO-1", "row1", "_svg 1", exp, sig[:-1] + "0", key=KEY, now=NOW))
		self.assertFalse(pf.verify_signature("SO-1", "row1", "_svg 1", exp, "", key=KEY, now=NOW))

	def test_expired_and_garbage(self):
		exp = int((NOW - timedelta(seconds=1)).timestamp())
		sig = pf.make_signature("SO-1", "row1", "p", exp, key=KEY)
		self.assertFalse(pf.verify_signature("SO-1", "row1", "p", exp, sig, key=KEY, now=NOW))
		self.assertFalse(pf.verify_signature("SO-1", "row1", "p", "soon", sig, key=KEY, now=NOW))
		self.assertFalse(pf.verify_signature("SO-1", "row1", "p", None, sig, key=KEY, now=NOW))

	def test_key_derivation_is_stable_and_secret_bound(self):
		self.assertEqual(pf.signing_key("abc"), pf.signing_key("abc"))
		self.assertNotEqual(pf.signing_key("abc"), pf.signing_key("abd"))
		with self.assertRaises(pf.PersonalizationFileError):
			pf.signing_key("")


class TestExpiryRule(unittest.TestCase):
	def test_preview_is_minutes_and_bounded(self):
		self.assertEqual(pf.expiry_for("preview", now=NOW), int(NOW.timestamp()) + pf.PREVIEW_TTL_SECONDS)
		self.assertEqual(pf.expiry_for("preview", ttl=120, now=NOW), int(NOW.timestamp()) + 120)
		self.assertEqual(pf.expiry_for("preview", ttl=10, now=NOW), int(NOW.timestamp()) + 60)
		self.assertEqual(pf.expiry_for("preview", ttl=99999, now=NOW), int(NOW.timestamp()) + pf.PREVIEW_MAX_TTL_SECONDS)

	def test_production_ends_with_the_k0_clock(self):
		exp = pf.expiry_for("production", purge_after="2026-12-03", now=NOW)
		self.assertEqual(datetime.fromtimestamp(exp, tz=timezone.utc), datetime(2026, 12, 3, 23, 59, 59, tzinfo=timezone.utc))
		with self.assertRaises(pf.PersonalizationFileError):
			pf.expiry_for("production", purge_after=None, now=NOW)


class TestReferenceUrl(unittest.TestCase):
	def test_shape(self):
		url = pf.reference_url("SO-SHP-2026-00029", "kco08v6idk", "_svg 1", base_url="https://erp.example")
		self.assertEqual(
			url,
			"https://erp.example/api/method/ecommerce_integrations.b2c.personalization_files.serve"
			"?so=SO-SHP-2026-00029&row=kco08v6idk&property=_svg+1",
		)
		signed = pf.reference_url("SO-1", "r", "p", 1700000000, "abc", base_url="https://erp.example")
		self.assertTrue(signed.endswith("?so=SO-1&row=r&property=p&exp=1700000000&sig=abc"))
		# no signature without an expiry, and vice versa
		self.assertNotIn("sig=", pf.reference_url("SO-1", "r", "p", None, "abc", base_url="https://x"))
