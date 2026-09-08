// Priority and shipping deadline in the Sales Order list (channel neutral fields of b2c.channel,
// 2026-09-08; before that the Amazon app showed its own Prime flag): two sortable columns plus a
// coloured label for a high priority, whatever channel raised it.
//
// Sortable comes from in_list_view (workflow_setup.CUSTOM_FIELDS): frappe's sort selector offers
// exactly the fields that are mandatory, bold or in_list_view (ui/sort_selector.js).
//
// ERPNext ASSIGNS frappe.listview_settings["Sales Order"] in its own sales_order_list.js, and
// this hook file is concatenated after it - so extend the object, never reassign it, or the
// status indicator and the bulk actions are gone.

frappe.listview_settings["Sales Order"] = frappe.listview_settings["Sales Order"] || {};

(function (settings) {
    const wanted = ["sales_channel", "integration_priority", "integration_ship_by"];
    const present = settings.add_fields || [];
    settings.add_fields = present.concat(wanted.filter((f) => !present.includes(f)));

    settings.formatters = Object.assign({}, settings.formatters, {
        integration_priority(value) {
            if (value !== "High") return "";
            return `<span class="indicator-pill blue" title="${__("Hohe Priorität (z. B. Amazon Prime, Express)")}">${__("High")}</span>`;
        },
    });
})(frappe.listview_settings["Sales Order"]);
