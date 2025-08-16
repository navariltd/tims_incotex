import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import IfNull


def execute():
    SalesInvoice = DocType("Sales Invoice")

    query = (
        frappe.qb.from_(SalesInvoice)
        .select(SalesInvoice.name)
        .where(
            (SalesInvoice.etr_serial_number.isnotnull())
            & (SalesInvoice.etr_serial_number != "")
            & (SalesInvoice.etr_invoice_number.isnotnull())
            & (SalesInvoice.etr_invoice_number != "")
            & ((IfNull(SalesInvoice.is_filed, 0) == 0))
        )
    )

    invoices_to_update = query.run(as_dict=True)

    for invoice in invoices_to_update:
        frappe.db.set_value(
            "Sales Invoice", invoice.name, "is_filed", 1, update_modified=False
        )
