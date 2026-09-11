"""The live-pull cursor only ever moves forward (2026-09-11).

Before: `newest` started at the overlap-shifted query start, so every run without a newer order
set the cursor one minute back - a quiet shop drifted 176 minutes behind its window start in two
days, and the cursor was useless as a sign of life.
"""

import contextlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import shopify
import shopify.collection

from ecommerce_integrations.shopify import connection, live_pull

ACCOUNT = "quiet.myshopify.com"


class FakeSetting:
	name = ACCOUNT

	def is_enabled(self):
		return True


class FakeOrder:
	def __init__(self, updated_at):
		self.updated_at = updated_at

	def to_dict(self):
		return {"name": "#1", "updated_at": self.updated_at.isoformat()}


class TestLivePullCursor(unittest.TestCase):
	def setUp(self):
		self.defaults = {}
		self.orders = []
		self.find_calls = []
		patches = [
			mock.patch.object(live_pull.frappe, "set_user"),
			mock.patch.object(live_pull.frappe, "get_doc", return_value=FakeSetting()),
			mock.patch.object(live_pull.frappe.db, "get_default", side_effect=self.defaults.get),
			mock.patch.object(live_pull.frappe.db, "set_default", side_effect=self.defaults.__setitem__),
			mock.patch.object(live_pull.frappe.db, "commit"),
			mock.patch.object(live_pull, "apply_order", return_value="unchanged"),
			mock.patch.object(connection, "get_temp_session_context", side_effect=lambda _s: contextlib.nullcontext()),
			mock.patch.object(shopify.Order, "find", side_effect=self._find, create=True),
			mock.patch.object(shopify.collection, "PaginatedIterator", side_effect=lambda first: [first]),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def _find(self, **kwargs):
		self.find_calls.append(kwargs)
		return list(self.orders)

	def cursor(self):
		return live_pull.get_cursor(ACCOUNT)

	def seed(self, cursor, window_start=None):
		self.defaults[live_pull.cursor_key(ACCOUNT)] = cursor.isoformat()
		self.defaults[live_pull.start_key(ACCOUNT)] = (window_start or cursor).isoformat()

	def test_quiet_run_keeps_the_cursor(self):
		stored = datetime(2026, 9, 9, 7, 15, 4, tzinfo=timezone.utc)
		self.seed(stored)
		for _ in range(3):
			live_pull.pull_account(ACCOUNT)
		self.assertEqual(self.cursor(), stored)
		# the query still re-reads one minute of overlap
		self.assertEqual(self.find_calls[-1]["updated_at_min"], (stored - timedelta(minutes=1)).isoformat())

	def test_newer_order_moves_the_cursor_forward(self):
		stored = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
		self.seed(stored)
		newer = stored + timedelta(minutes=7)
		self.orders = [FakeOrder(stored - timedelta(seconds=30)), FakeOrder(newer)]
		live_pull.pull_account(ACCOUNT)
		self.assertEqual(self.cursor(), newer)

	def test_manual_reread_does_not_rewind(self):
		stored = datetime.now(timezone.utc) - timedelta(minutes=5)
		self.seed(stored, window_start=stored - timedelta(days=2))
		live_pull.pull_account(ACCOUNT, minutes=180)
		self.assertEqual(self.cursor(), stored)
		self.assertLess(self.find_calls[-1]["updated_at_min"], (stored - timedelta(minutes=170)).isoformat())

	def test_first_run_starts_at_the_window_not_before_it(self):
		result = live_pull.pull_account(ACCOUNT)
		window_start = live_pull.get_window_start(ACCOUNT)
		self.assertIsNotNone(window_start)
		self.assertEqual(self.cursor(), window_start)
		self.assertEqual(result["cursor"], window_start.isoformat())
