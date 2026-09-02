# Copyright (c) 2021, Frappe and Contributors
# See LICENSE

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from frappe.utils import get_system_timezone

from ecommerce_integrations.shopify.constants import (
	CONTACT_MARKETING_CONSENT_AT_FIELD,
	CONTACT_MARKETING_OPT_IN_LEVEL_FIELD,
	CONTACT_MARKETING_STATE_FIELD,
)
from ecommerce_integrations.shopify.customer import marketing_consent_fields


class TestMarketingConsent(unittest.TestCase):
	def test_subscribed_clears_unsubscribed_and_keeps_the_evidence(self):
		fields = marketing_consent_fields(
			{
				"email_marketing_consent": {
					"state": "subscribed",
					"opt_in_level": "confirmed_opt_in",
					"consent_updated_at": "2026-08-30T10:15:00+02:00",
				}
			}
		)

		self.assertEqual(fields["unsubscribed"], 0)
		self.assertEqual(fields[CONTACT_MARKETING_STATE_FIELD], "subscribed")
		self.assertEqual(fields[CONTACT_MARKETING_OPT_IN_LEVEL_FIELD], "confirmed_opt_in")

		# stored in the site's timezone, offset dropped - what a Datetime column accepts
		expected = (
			datetime.fromisoformat("2026-08-30T10:15:00+02:00")
			.astimezone(ZoneInfo(get_system_timezone()))
			.replace(tzinfo=None)
		)
		self.assertEqual(fields[CONTACT_MARKETING_CONSENT_AT_FIELD], expected)
		self.assertIsNone(fields[CONTACT_MARKETING_CONSENT_AT_FIELD].tzinfo)

	def test_anything_but_subscribed_stays_unsubscribed(self):
		for state in ("not_subscribed", "unsubscribed", "pending", "redacted", "invalid"):
			with self.subTest(state=state):
				fields = marketing_consent_fields(
					{"email_marketing_consent": {"state": state, "opt_in_level": "confirmed_opt_in"}}
				)
				self.assertEqual(fields["unsubscribed"], 1)
				self.assertEqual(fields[CONTACT_MARKETING_STATE_FIELD], state)

	def test_missing_consent_block_is_fail_safe(self):
		# customers without e-mail carry no block at all (63 of 14,405 in the live shop)
		fields = marketing_consent_fields({"first_name": "Anna"})

		self.assertEqual(fields["unsubscribed"], 1)
		self.assertIsNone(fields[CONTACT_MARKETING_STATE_FIELD])
		self.assertIsNone(fields[CONTACT_MARKETING_OPT_IN_LEVEL_FIELD])
		self.assertIsNone(fields[CONTACT_MARKETING_CONSENT_AT_FIELD])

	def test_legacy_accepts_marketing_is_ignored(self):
		# the removed legacy flag has no timestamp and must not count as consent
		fields = marketing_consent_fields({"accepts_marketing": True})

		self.assertEqual(fields["unsubscribed"], 1)
		self.assertIsNone(fields[CONTACT_MARKETING_STATE_FIELD])
