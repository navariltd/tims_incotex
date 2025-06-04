# Copyright (c) 2025, Mania and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe.model.document import Document
from tims_incotex.tims_incotex.utils import get_tims_settings


class TimsIncotexSettings(Document):
    pass


@frappe.whitelist()
def health_check(company):
    """Checks the TIMS API health status"""
    settings = get_tims_settings(company)
    url = settings.get("api_url") + settings.get("health_check_endpoint")
    try:
        response = requests.get(url, timeout=5)
        response_data = response.json()

        return {
            "message": response_data.get("message", "Unknown"),
            "status": response_data.get("status", "99"),
            "description": response_data.get("description", "No response description."),
        }

    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Health Check Failed: {str(e)}", "TIMS Health Check")
        return {
            "message": "Error",
            "status": "99",
            "description": f"Failed to connect: {str(e)}",
        }
