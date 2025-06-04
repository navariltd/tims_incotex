// Copyright (c) 2025, Mania and contributors
// For license information, please see license.txt

frappe.ui.form.on("Tims Incotex Settings", {
	refresh: function (frm) {
		frm.add_custom_button(
			__("Health Check"),
			function () {
				frappe.call({
					method: "tims_incotex.tims_incotex.doctype.tims_incotex_settings.tims_incotex_settings.health_check",
					args: { company: frm.doc.company },
					callback: function (r) {
						if (r.message) {
							frappe.msgprint({
								title: __("Health Check Status"),
								indicator: r.message.status === "00" ? "green" : "red",
								message: __(r.message.description),
							});
						}
					},
				});
			},
			__("Actions")
		);
	},
});
