import frappe


def before_save(doc, method=None):
    if not doc.tax_id:
        return

    filters = {"tax_id": doc.tax_id, "name": ["!=", doc.name]}

    if frappe.db.exists("Customer", filters):
        frappe.throw(f"Customer with Tax ID '{doc.tax_id}' already exists.")
