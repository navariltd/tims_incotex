import json
import re
import time
from base64 import b64encode
from io import BytesIO

import frappe
import qrcode
import requests
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.utils import cint

from tims_incotex.tims_incotex.utils import get_tims_settings

SEND_INVOICE_TO_TIMS = "tims_incotex.tims_incotex.api.sales_invoice.send_invoice_to_tims"


class TimsInvoice:
	def __init__(self, invoice_name, company):
		"""Initialize with Sales Invoice document."""
		self.invoice = frappe.get_doc("Sales Invoice", invoice_name)
		self.settings = get_tims_settings(company)

	def can_sign(self):
		"""Return True if this invoice is eligible for TIMS signing."""
		if self.invoice.is_opening == "Yes":
			frappe.logger().info(f"Skipping TIMS signing for opening invoice {self.invoice.name}")
			return False

		if self.invoice.etr_invoice_number:
			frappe.logger().info(f"Invoice {self.invoice.name} already signed, skipping.")
			return False

		return True

	def sign_invoice(self, *, enqueue_after_commit=True):
		"""Request TIMS signing by enqueueing a background job."""
		if not self.can_sign():
			return

		enqueue_invoice_signing(
			self.invoice.name,
			self.invoice.company,
			enqueue_after_commit=enqueue_after_commit,
		)

	def _send_to_api(self):
		"""Send invoice data to TIMS API and handle response."""
		endpoint = get_endpoint(self.invoice, company=self.invoice.company)
		url = f"{self.settings['api_url']}{endpoint}"
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Basic {self.settings['api_key']}",
		}
		payload = self._prepare_payload()

		if not url or not headers or not payload:
			frappe.log_error("Missing URL, headers, or payload for TIMS API.", "TimsInvoice Error")
			return

		integration_request = create_request_log(
			data=payload,
			is_remote_request=True,
			service_name="TIMS Incotex",
			request_headers=headers,
			url=url,
			reference_docname=self.invoice.name,
			reference_doctype="Sales Invoice",
		)

		try:
			response = requests.post(url, json=payload, headers=headers, timeout=(5, 30))
			response.raise_for_status()
			response_data = response.json()

			if response_data.get("description") == "Signed successfully.":
				integration_request.handle_success(json.dumps(response_data))
				self._update_invoice(response_data)
			else:
				error_msg = response_data.get("error_status", "Unknown error")
				failure_data = {"error": error_msg, "response": response_data}
				integration_request.handle_failure(json.dumps(failure_data))
				self.handle_failure(response_data)

		except requests.exceptions.RequestException as e:
			error_msg = f"API request failed: {e!s}"
			failure_data = {"error": error_msg, "response": None}
			integration_request.handle_failure(json.dumps(failure_data))
			self._log_error(error_msg)

	def _prepare_payload(self):
		"""Prepare invoice data for TIMS API."""
		rel_doc_number = get_relevant_invoice_number(self.invoice)

		return {
			"invoice_date": self.invoice.posting_date.strftime("%d-%m-%Y"),
			"invoice_number": self.invoice.invoice_number,
			"invoice_pin": self.settings["company_pin"],
			"customer_pin": self.invoice.tax_id or "",
			"customer_exid": "",
			"grand_total": f"{abs(self.invoice.base_grand_total)}",
			"net_subtotal": f"{abs(self.invoice.base_net_total)}",
			"tax_total": f"{abs(self.invoice.base_total_taxes_and_charges)}",
			"net_discount_total": f"{abs(self.invoice.base_discount_amount or 0.00)}",
			"sel_currency": currency_code(self.invoice.currency),
			"rel_doc_number": rel_doc_number,
			"items_list": [
				f"{i.custom_hs_code} "
				f"{re.sub(r'[^a-zA-Z0-9]', '', i.item_code)} {abs(i.qty):.2f} {abs(i.base_rate):.3f} {abs(i.base_amount):.3f}"
				for i in self.invoice.items
			],
		}

	def _update_invoice(self, response_data):
		"""Update invoice with TIMS API response"""
		frappe.db.set_value(
			"Sales Invoice",
			self.invoice.name,
			{
				"etr_serial_number": response_data.get("cu_serial_number"),
				"etr_invoice_number": response_data.get("cu_invoice_number"),
				"custom_verify_url": response_data.get("verify_url"),
				"custom_signing_status": "Signed",
				"custom_tims_response_description": response_data.get(
					"message", "Invoice signed successfully."
				),
				"custom_qr_code": get_qr_code(response_data.get("verify_url")),
				"cu_invoice_date": frappe.utils.today(),
				"is_filed": 1,
			},
			update_modified=False,
		)

	def handle_failure(self, response_data):
		"""Handle failed API response."""
		frappe.log_error("Tims Error", f"Failed to sign invoice: {response_data.get('message')}")

		frappe.db.set_value(
			"Sales Invoice",
			self.invoice.name,
			{
				"custom_signing_status": "Failed",
				"custom_tims_response_description": response_data.get("message"),
			},
		)

	def _log_error(self, message):
		"""Log API errors."""
		frappe.log_error("TimsInvoice Error", f"TIMS API Error: {message}")

		frappe.db.set_value(
			"Sales Invoice",
			self.invoice.name,
			{
				"custom_signing_status": "Failed",
				"custom_tims_response_description": message,
			},
		)


def enqueue_invoice_signing(invoice_name, company, *, enqueue_after_commit=False):
	"""Queue TIMS signing for an invoice. Shared by submit, UI, and retry paths."""
	frappe.enqueue(
		SEND_INVOICE_TO_TIMS,
		enqueue_after_commit=enqueue_after_commit,
		job_name=f"Sign Invoice {invoice_name}",
		invoice_name=invoice_name,
		company=company,
		queue="default",
		timeout=300,
	)


def send_invoice_to_tims(invoice_name, company):
	"""Background worker: load invoice and send it to TIMS."""
	tims = TimsInvoice(invoice_name, company)
	if not tims.can_sign():
		return
	tims._send_to_api()


def on_submit(doc, method):
	"""Trigger invoice signing on submission."""
	if frappe.db.exists("Tims Incotex Settings", {"company": doc.company}):
		if is_active(doc.company):
			TimsInvoice(doc.name, doc.company).sign_invoice(enqueue_after_commit=True)


@frappe.whitelist()
def sign_single_invoice(invoice_name, company):
	"""Public function to trigger invoice signing."""
	if is_active(company):
		TimsInvoice(invoice_name, company).sign_invoice(enqueue_after_commit=True)


@frappe.whitelist()
def retry_pending_invoices(batch_size=100):
	"""Enqueue a limited batch of failed/unsigned invoices for TIMS signing.

	Avoids loading full Sales Invoice docs in the scheduler job so large
	backlogs do not hit the RQ job timeout. Eligibility is re-checked in the worker.
	"""
	pending_invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"is_opening": "No",
			"custom_signing_status": ["in", ["Failed", ""]],
			"etr_invoice_number": ["in", ["", None]],
		},
		fields=["name", "company"],
		order_by="modified desc",
		limit_page_length=cint(batch_size),
	)

	active_companies = {}
	for inv in pending_invoices:
		company = inv.company
		if company not in active_companies:
			active_companies[company] = bool(is_active(company))

		if not active_companies[company]:
			continue

		enqueue_invoice_signing(inv.name, company)


@frappe.whitelist()
def get_invoice(invoice, company):
	settings = get_tims_settings(company)
	if not settings:
		frappe.throw("TIMS settings not configured for this company.")

	url = settings.get("api_url") + settings.get("query_endpoint")
	headers = {
		"Content-Type": "application/json",
		"Authorization": f"Basic {settings['api_key']}",
	}
	payload = {
		"invoice_number": invoice,
		"username": settings.get("username"),
		"password": settings.get("password"),
	}

	try:
		response = requests.post(url, json=payload, headers=headers, timeout=10)
		response_data = response.json()

		if response_data.get("status") != "00":
			frappe.log_error(f"TIMS Query Failed: {response_data}", "TIMS Invoice Query")
			return {
				"message": "Query failed",
				"status": response_data.get("status", "99"),
				"description": response_data.get("description", "Unknown error"),
			}

		return response_data

	except requests.exceptions.RequestException as e:
		frappe.log_error(f"Failed to connect: {e!s}", "TIMS Health Check")
		return {
			"message": "Error",
			"status": "99",
			"description": f"Failed to connect: {e!s}",
		}


def get_qr_code(data: str) -> str:
	"""Generate QR Code data."""
	qr_code_bytes = get_qr_code_bytes(data, format="PNG")
	base64_string = bytes_to_base64_string(qr_code_bytes)
	return add_file_info(base64_string)


def add_file_info(data: str) -> str:
	"""Add info about the file type and encoding."""
	return f"data:image/png;base64, {data}"


def get_qr_code_bytes(data: bytes | str, format: str = "PNG") -> bytes:
	"""Create a QR code and return the bytes."""
	img = qrcode.make(data)
	buffered = BytesIO()
	img.save(buffered, format=format)
	return buffered.getvalue()


def bytes_to_base64_string(data: bytes) -> str:
	"""Convert bytes to a base64 encoded string."""
	return b64encode(data).decode("utf-8")


def format_time_for_invoice(time: str) -> str:
	"""Format time to ensure leading zero for single-digit hours."""
	hour, minute, second = time.split(":")
	return f"{int(hour):02d}:{minute}:{second}"


def get_endpoint(invoice, company):
	settings = get_tims_settings(company)
	endpoint = ""
	if invoice.is_return and inclusive_invoice(invoice):
		endpoint = settings.get("credit_note_inclusive")
	elif invoice.is_return and not inclusive_invoice(invoice):
		endpoint = settings.get("credit_note_exclusive")
	elif invoice.is_debit_note:
		endpoint = "sign?debit"
	elif not invoice.is_return and inclusive_invoice(invoice):
		endpoint = settings.get("invoice_inclusive")
	elif not invoice.is_return and not inclusive_invoice(invoice):
		endpoint = settings.get("invoice_exclusive")
	return endpoint


def is_active(company):
	settings = get_tims_settings(company)
	if settings:
		return settings.get("active")
	return None


def tax_amount(invoice):
	hs_code = ""
	if not invoice.total_taxes_and_charges:
		customer = invoice.customer
		tax_category = frappe.get_value("Customer", customer, "tax_category")
		hs_code = frappe.get_value("Tax Category", tax_category, "custom_hs_code")
	return hs_code if hs_code else ""


def currency_code(currency):
	if currency == "KES":
		return "Ksh"
	return "Ksh"


def format_invoice_number(doc, method=None):
	"""Format the invoice number to ensure there are no special characters."""
	invoice_number = re.sub(r"[^a-zA-Z0-9]", "", doc.name)

	frappe.db.set_value(
		"Sales Invoice",
		doc.name,
		"invoice_number",
		invoice_number,
		update_modified=False,
	)
	doc.invoice_number = invoice_number


def inclusive_invoice(invoice):
	"""Determine if the invoice is inclusive or exclusive based on the TIMS settings."""
	inclusive = False

	if invoice.taxes:
		taxes = invoice.taxes[0]
		if taxes.included_in_print_rate == 1:
			inclusive = True
		else:
			inclusive = False

	return inclusive


def is_valid_kra_pin(pin: str) -> bool:
	"""Checks if the string provided conforms to the pattern of a KRA PIN."""
	pattern = r"^[a-zA-Z]{1}[0-9]{9}[a-zA-Z]{1}$"
	return bool(re.match(pattern, pin))


def before_save(doc, method):
	remove_tims(doc)
	if doc.customer and doc.tax_id:
		if not is_valid_kra_pin(doc.tax_id):
			frappe.throw("Invalid KRA PIN format. Please enter a valid KRA PIN.")
	format_invoice_number(doc)
	get_hs_code_before_save(doc)


def remove_tims(doc):
	if doc.is_new():
		doc.is_filed = None
		doc.etr_serial_number = None
		doc.etr_invoice_number = None
		doc.custom_verify_url = None
		doc.custom_signing_status = None
		doc.custom_tims_response_description = None
		doc.custom_qr_code = None
		doc.cu_invoice_date = None


def prevent_cancel_signed_invoice(doc, method):
	if doc.custom_signing_status == "Signed":
		frappe.throw(
			_("🚫 Cannot cancel the document as it is already <b>signed</b> ✅ and sent to TIMS 📤.")
		)


def get_relevant_invoice_number(doc):
	"""Get the relevant invoice number based on the invoice type."""
	custom_relevant_invoice_number = ""
	if doc.is_return:
		custom_relevant_invoice_number = doc.custom_relevant_invoice_number
		if not doc.custom_relevant_invoice_number and doc.return_against:
			invoice = frappe.get_doc("Sales Invoice", doc.return_against)
			if invoice.etr_invoice_number:
				custom_relevant_invoice_number = invoice.etr_invoice_number

	return custom_relevant_invoice_number


def get_hs_code_before_save(doc):
	for item in doc.items:
		hs_code = get_hs_code_item_tax(item.item_code, item.item_tax_template)
		item.custom_hs_code = hs_code or ""


def get_hs_code_item_tax(item_code, item_tax_template_name=None):
	"""
	Retrieve HS Code from Item Tax child table by checking:
	1. If item has Item Tax row matching the provided item_tax_template.
	2. If not, fallback to any Item Tax for the item.
	3. If not found, fallback to Item Group's Item Tax row.
	"""
	hs_code = ""

	# 1. Check specific template
	if item_tax_template_name:
		hs_code = frappe.db.get_value(
			"Item Tax",
			{"parent": item_code, "item_tax_template": item_tax_template_name},
			"tims_hscode",
		)
		if hs_code:
			return hs_code

	# 2. Check any item tax for this item
	item_tax = frappe.get_all("Item Tax", filters={"parent": item_code}, fields=["tims_hscode"], limit=1)
	if item_tax and item_tax[0].tims_hscode:
		return item_tax[0].tims_hscode

	# 3. Check item group taxes
	item_group = frappe.db.get_value("Item", item_code, "item_group")
	if item_group:
		group_tax = frappe.get_all(
			"Item Tax", filters={"parent": item_group}, fields=["tims_hscode"], limit=1
		)
		if group_tax and group_tax[0].tims_hscode:
			return group_tax[0].tims_hscode

	return hs_code


def batched(invoices: list[str], size: int):
	for i in range(0, len(invoices), size):
		yield i // size + 1, invoices[i : i + size]


@frappe.whitelist()
def retry_pending_invoices_with_delay(batch_size=100):
	pending_invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"is_opening": "No",
			"custom_signing_status": ["in", ["Failed", ""]],
			"etr_invoice_number": ["in", ["", None]],
		},
		fields=["name", "company"],
		order_by="modified desc",
		limit_page_length=cint(500),
	)

	for batch_number, batch_invoices in batched(pending_invoices, batch_size):
		frappe.enqueue(
			"tims_incotex.tims_incotex.api.sales_invoice.send_invoice_batch_to_tims",
			invoice_list=batch_invoices,
			queue="default",
			timeout=600,
			job_name=f"Retry TIMS invoices {batch_number}",
		)
		time.sleep(20)


def send_invoice_batch_to_tims(invoice_list):
	for invoice in invoice_list:
		send_invoice_to_tims(invoice.name, invoice.company)
