import frappe


def get_tims_settings(company):
    """Fetch TIMS Incotex Settings from the doctype. If not found, return None."""
    try:
        settings = frappe.get_doc("Tims Incotex Settings", {"company": company})
    except frappe.DoesNotExistError:
        return  # Or you could return {} if you prefer an empty dict

    return {
        "company": settings.company,
        "company_pin": settings.company_pin,
        "api_url": settings.server_base_url,
        "query_endpoint": settings.query_endpoint,
        "health_check_endpoint": settings.health_check_endpoint,
        "password": settings.get_password(fieldname="api_key", raise_exception=False),
        "username": settings.get_password(fieldname="api_key", raise_exception=False),
        "api_key": settings.get_password(fieldname="api_key", raise_exception=False),
        "active": settings.active,
        "invoice_inclusive": settings.invoice_inclusive,
        "invoice_exclusive": settings.invoice_exclusive,
        "credit_note_inclusive": settings.credit_note_inclusive,
        "credit_note_exclusive": settings.credit_note_exclusive,
    }
