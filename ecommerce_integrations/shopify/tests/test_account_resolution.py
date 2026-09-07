import types
import unittest
from unittest.mock import patch

from ecommerce_integrations.shopify.utils import get_company_shopify_account

COMPANY = "Yücel & Tirgil GbR"


class _Account:
	def __init__(self, name, enabled):
		self.name = name
		self._enabled = enabled

	def is_enabled(self):
		return bool(self._enabled)


def _db(accounts):
	"""Stand-in for frappe.db.get_value over a list of (name, enabled)."""

	def get_value(doctype, filters, fieldname):
		rows = [a for a in accounts if a[0] is not None]
		if filters.get("enable_shopify") == 1:
			rows = [a for a in rows if a[1]]
		return rows[0][0] if rows else None

	return get_value


class TestCompanyAccountResolution(unittest.TestCase):
	"""One disabled account next to an enabled one blocked item creation for a
	whole weekend on b2c.local (109 orders, 'integration is disabled')."""

	def _resolve(self, accounts):
		# frappe.db is a Local proxy and is unbound outside a site context, so the
		# whole object is replaced rather than one of its methods.
		with (
			patch("frappe.db", types.SimpleNamespace(get_value=_db(accounts))),
			patch("frappe.get_doc", side_effect=lambda dt, name: _Account(name, dict(accounts)[name])),
		):
			return get_company_shopify_account(COMPANY)

	def test_prefers_the_enabled_account_over_a_disabled_one(self):
		# The disabled store sorts first, which is exactly how the bug surfaced.
		account = self._resolve([("mit-bildgravur-de.myshopify.com", 0), ("z6wkr3-yk.myshopify.com", 1)])
		self.assertEqual(account.name, "z6wkr3-yk.myshopify.com")
		self.assertTrue(account.is_enabled())

	def test_falls_back_to_a_disabled_account_when_none_is_enabled(self):
		# Previous behaviour for single-account companies must not change.
		account = self._resolve([("mit-bildgravur-de.myshopify.com", 0)])
		self.assertEqual(account.name, "mit-bildgravur-de.myshopify.com")

	def test_returns_none_without_any_account(self):
		self.assertIsNone(self._resolve([]))
