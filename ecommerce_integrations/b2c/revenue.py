"""Revenue account per brand (accounting doc §8b no. 5).

ERPNext resolves the income account per invoice line, and the usual place to configure it is the
Item Default. That does not carry the brand: 1.089 of 2.858 live products are sold on Shopify *and*
on Amazon (bauplan 2026-09-01), so one item would need two revenue accounts. The brand belongs to
the ORDER, not to the item - so the invoice stage writes it, reading the accounts from the Sales
Channel the order came from (b2c.channel, read contract).

Deliveries to Austria book on their own revenue account (tax advisor 2026-09-04: 8320, while 1754
holds the Austrian VAT until it is paid), so the shipping country decides between the two.
"""

import frappe

from ecommerce_integrations.b2c import channel

AUSTRIA = "Austria"


def account_for(country, income_account, income_account_at):
	"""The rule itself, free of the database: Austria takes its own account when one is configured."""
	if country == AUSTRIA and income_account_at:
		return income_account_at
	return income_account


def channel_accounts(so):
	"""(income_account, income_account_at) of the Sales Channel this order came from."""
	row = channel.values(so, "income_account", "income_account_at")
	return row.get("income_account"), row.get("income_account_at")


def shipping_country(so):
	address = so.get("shipping_address_name") or so.get("customer_address")
	return address and frappe.db.get_value("Address", address, "country")


def apply(invoice, so):
	"""Write the brand's revenue account on every line before the invoice is saved. Returns the
	account that was used, or None when the channel carries none - then the company default applies
	and the caller says so, rather than booking a brand silently onto the collective account."""
	income_account, income_account_at = channel_accounts(so)
	if not (income_account or income_account_at):
		return None
	account = account_for(shipping_country(so), income_account, income_account_at)
	if not account:
		return None
	for row in invoice.items:
		row.income_account = account
	return account
