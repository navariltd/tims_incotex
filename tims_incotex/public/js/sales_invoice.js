frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		// Show "Submit e-Invoice" button when invoice is pending or failed
		if (
			frm.doc.docstatus === 1 &&
			["Failed", "Pending", ""].includes(frm.doc.custom_signing_status)
		) {
			frm.add_custom_button(
				"Submit e-Invoice",
				function () {
					frappe.call({
						method: "tims_incotex.tims_incotex.api.sales_invoice.sign_single_invoice",
						args: { invoice_name: frm.doc.name, company: frm.doc.company },
						callback: function (r) {
							if (!r.exc) {
								frappe.msgprint({
									title: __("Success"),
									message: __("Invoice submitted to TIMS."),
									indicator: "green",
								});
								frm.reload_doc();
							}
						},
					});
				},
				__("Tims Actions")
			);
		}

		// Show "Get e-Invoice" if already signed
		if (frm.doc.docstatus === 1 && frm.doc.custom_signing_status === "Signed") {
			frm.add_custom_button(
				"Get e-Invoice",
				function () {
					frappe.call({
						method: "tims_incotex.tims_incotex.api.sales_invoice.get_invoice",
						args: { invoice: frm.doc.name, company: frm.doc.company },
						callback: function (r) {
							if (!r.exc && r.message) {
								let response = r.message;

								if (response.status === "00") {
									frappe.msgprint({
										title: __("Success"),
										message: __(
											"Invoice retrieved successfully.<br><b>CU Invoice No:</b> {0}<br><b>Status:</b> {1}<br><b>Description:</b> {2}",
											[
												response["cu invoice number"],
												response.status,
												response.description,
											]
										),
										indicator: "green",
									});
								} else {
									frappe.msgprint({
										title: __("Error"),
										message: __(
											"Invoice retrieval failed.<br><b>Status:</b> {0}<br><b>Description:</b> {1}",
											[response.status, response.description]
										),
										indicator: "red",
									});
								}
							}
						},
					});
				},
				__("Tims Actions")
			);
		}
	},
});
