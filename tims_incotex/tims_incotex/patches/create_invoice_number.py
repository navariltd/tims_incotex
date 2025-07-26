import re

import frappe


def execute():
    """Set Invoice Number On Credit Notes"""

    invoices = frappe.get_all(
        "Sales Invoice",
        filters={"invoice_number": ["in", [None, ""]], "status": "Return"},
        fields=["name"],
    )

    if not invoices:
        return

    for invoice in invoices:
        formatted_invoice_number = re.sub(r"[^a-zA-Z0-9]", "", invoice.name)

        frappe.db.set_value(
            "Sales Invoice",
            invoice.name,
            "invoice_number",
            formatted_invoice_number,
            update_modified=False,
        )
