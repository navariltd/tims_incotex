import frappe


def before_save(doc, method=None):
    if frappe.db.exists("Customer", {"tax_id": doc.tax_id}):
        frappe.throw(f"Customer with Tax ID '{doc.tax_id}' already exists.")
