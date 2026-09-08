// The integration picker offers only the account DocTypes that registered themselves through
// the sales_channel_integrations hook; the server validates the same list.
frappe.ui.form.on("Sales Channel", {
	setup(frm) {
		frm.set_query("integration_type", () => ({
			filters: { name: ["in", frm._integration_doctypes || []] },
		}));
	},
	onload(frm) {
		frappe.call("ecommerce_integrations.b2c.channel.integration_doctypes").then((r) => {
			frm._integration_doctypes = r.message || [];
		});
	},
});
