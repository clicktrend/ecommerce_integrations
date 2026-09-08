"""Sales Channel: brand x integration, the record every consumer of an order reads.

The DocType is the read side of the channel interface (see b2c.channel): whatever the rest of
the system needs to know about the shop an order came from - company, revenue accounts, mail
sender, logo, whether the address is checked - lives here. The connector account behind it
(`integration_type` + `integration`) keeps only transport: credentials, cursors, webhooks.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from ecommerce_integrations.b2c import channel


class SalesChannel(Document):
	def validate(self):
		self.code = (self.code or "").strip()
		self.validate_integration()

	def validate_integration(self):
		if bool(self.integration_type) != bool(self.integration):
			frappe.throw(_("Integration und Konto gehören zusammen: beides angeben oder beides leer lassen."))
		if not self.integration_type:
			return
		registered = channel.integration_doctypes()
		if self.integration_type not in registered:
			frappe.throw(
				_("{0} ist keine registrierte Integration. Registriert über den Hook {1}: {2}").format(
					self.integration_type, channel.HOOK, ", ".join(registered) or "-"
				)
			)
		# One account serves exactly one channel (Marello: SalesChannel.integrationChannel is OneToOne).
		other = frappe.db.get_value(
			"Sales Channel",
			{"integration_type": self.integration_type, "integration": self.integration, "name": ["!=", self.name]},
			"name",
		)
		if other:
			frappe.throw(
				_("Das Konto {0} ({1}) wird bereits vom Sales Channel {2} bedient.").format(
					self.integration, self.integration_type, other
				)
			)
