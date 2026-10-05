import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from ecommerce_integrations.controllers import scheduling
from ecommerce_integrations.controllers.scheduling import need_to_run

NOW = datetime(2026, 10, 5, 12, 0)


def _db(interval, last_run):
	db = types.SimpleNamespace(
		get_single_value=MagicMock(side_effect=lambda dt, field, cache=False: {"freq": interval, "last": last_run}[field]),
		get_value=MagicMock(side_effect=lambda dt, name, field, cache=False: {"freq": interval, "last": last_run}[field]),
		set_value=MagicMock(),
	)
	return db


class TestNeedToRun(unittest.TestCase):
	# No site context: frappe.db is a Local proxy and is unbound here, so the whole object is
	# replaced; the clock is frozen because frappe's now() reads the site's time zone.

	def setUp(self):
		clock = [
			patch.object(scheduling, "now", lambda: NOW.isoformat(sep=" ")),
			patch.object(scheduling, "get_datetime", lambda value=None: NOW if value is None else value),
		]
		for p in clock:
			p.start()
			self.addCleanup(p.stop)

	def test_single_doctype_reads_the_single_values(self):
		db = _db(interval=10, last_run=None)
		with patch("frappe.db", db):
			self.assertTrue(need_to_run("Unicommerce Settings", None, "freq", "last"))

		db.get_value.assert_not_called()
		db.set_value.assert_called_once()
		self.assertEqual(db.set_value.call_args.args[:3], ("Unicommerce Settings", None, "last"))

	def test_account_reads_its_own_record(self):
		db = _db(interval=10, last_run=None)
		with patch("frappe.db", db):
			self.assertTrue(need_to_run("Shopify Account", "shop-a.myshopify.com", "freq", "last"))

		db.get_single_value.assert_not_called()
		self.assertEqual(db.set_value.call_args.args[:3], ("Shopify Account", "shop-a.myshopify.com", "last"))

	def test_does_not_run_inside_the_interval(self):
		db = _db(interval=10, last_run=NOW - timedelta(minutes=2))
		with patch("frappe.db", db):
			self.assertFalse(need_to_run("Unicommerce Settings", None, "freq", "last"))

		db.set_value.assert_not_called()
