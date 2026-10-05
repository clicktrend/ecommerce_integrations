import types
import unittest
from unittest.mock import MagicMock, patch

from ecommerce_integrations.patches import update_shopify_custom_fields as legacy_patch


class TestLegacyCustomFieldPatch(unittest.TestCase):
	"""patches.txt still runs this patch; it must import and run without the removed singleton."""

	def _run(self, stored_value):
		setup = MagicMock()
		db = types.SimpleNamespace(get_value=MagicMock(return_value=stored_value))
		with patch("frappe.db", db), patch.object(legacy_patch, "setup_custom_fields", setup):
			legacy_patch.execute()
		return setup

	def test_sets_up_the_fields_when_the_old_connector_was_enabled(self):
		self._run("1").assert_called_once()

	def test_does_nothing_on_a_site_without_the_old_connector(self):
		self._run(None).assert_not_called()
		self._run("0").assert_not_called()
