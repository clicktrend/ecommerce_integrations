import types
import unittest
from unittest.mock import patch

from ecommerce_integrations.shopify.utils import get_company_shopify_account

COMPANY = "_Test Company"


class _Account:
	def __init__(self, name, enabled):
		self.name = name
		self._enabled = enabled

	def is_enabled(self):
		return bool(self._enabled)


def _db(accounts):
	"""Stand-in for frappe.db.get_value over a list of (name, enabled), in table order."""

	def get_value(doctype, filters, fieldname):
		rows = list(accounts)
		if filters.get("enable_shopify") == 1:
			rows = [a for a in rows if a[1]]
		return rows[0][0] if rows else None

	return get_value


class TestCompanyAccountResolution(unittest.TestCase):
	def _resolve(self, accounts):
		# frappe.db is a Local proxy and is unbound outside a site context, so the whole
		# object is replaced rather than one of its methods.
		with (
			patch("frappe.db", types.SimpleNamespace(get_value=_db(accounts))),
			patch("frappe.get_doc", side_effect=lambda dt, name: _Account(name, dict(accounts)[name])),
		):
			return get_company_shopify_account(COMPANY)

	def test_prefers_the_enabled_account_over_a_disabled_one(self):
		# The disabled store comes first in the table.
		account = self._resolve([("retired.myshopify.com", 0), ("running.myshopify.com", 1)])
		self.assertEqual(account.name, "running.myshopify.com")

	def test_falls_back_to_a_disabled_account_when_none_is_enabled(self):
		account = self._resolve([("retired.myshopify.com", 0)])
		self.assertEqual(account.name, "retired.myshopify.com")

	def test_returns_none_without_any_account(self):
		self.assertIsNone(self._resolve([]))
