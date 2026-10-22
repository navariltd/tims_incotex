import json
import re
from base64 import b64encode
from io import BytesIO

import frappe
import qrcode
import requests
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.utils import add_to_date, cint, now_datetime
from urllib3.exceptions import NewConnectionError

from tims_incotex.tims_incotex.utils import get_tims_settings

SEND_INVOICE_TO_TIMS = "tims_incotex.tims_incotex.api.sales_invoice.send_invoice_to_tims"
CHECK_INVOICE_ON_TIMS = "tims_incotex.tims_incotex.api.sales_invoice.check_invoice_on_tims"

# Signing statuses. An invoice is only ever sent from one of SENDABLE, and the move to SUBMITTING is
# taken under a row lock, so two workers (or a worker and the hourly retry) cannot both send it.
SUBMITTING = "Submitting"
UNCONFIRMED = "Unconfirmed"
SIGNED = "Signed"
FAILED = "Failed"
SENDABLE = ("", "Pending", FAILED)

# A send that started this long ago and never finished (worker killed mid-request) is checked on TIMS.
STALE_SUBMITTING_MINUTES = 15

# Requests errors raised before anything reached the device: safe to send again.
NOT_SENT_ERRORS = (
	requests.exceptions.ConnectTimeout,
	requests.exceptions.URLRequired,
	requests.exceptions.MissingSchema,
	requests.exceptions.InvalidSchema,
	requests.exceptions.InvalidURL,
)


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
		"""Request TIMS signing immediately, or by enqueueing a background job."""
		if not self.can_sign():
			return

		if self.settings.get("sign_on_submit"):
			# Inside the submit transaction: the invoice row is already ours, so no separate commit.
			self._send_to_api(commit_claim=False)
			return

		enqueue_invoice_signing(
			self.invoice.name,
			self.invoice.company,
			enqueue_after_commit=enqueue_after_commit,
		)

	def _claim(self, allowed):
		"""Move the invoice to Submitting if it is still in one of `allowed` and unsigned.

		The row is read with a lock, so a second worker waits here and then sees Submitting.
		"""
		stored = frappe.db.get_value(
			"Sales Invoice",
			self.invoice.name,
			["custom_signing_status", "etr_invoice_number"],
			as_dict=True,
			for_update=True,
		)
		if not stored or stored.etr_invoice_number:
			return False
		if (stored.custom_signing_status or "") not in allowed:
			return False

		frappe.db.set_value(
			"Sales Invoice",
			self.invoice.name,
			{"custom_signing_status": SUBMITTING, "custom_tims_response_description": ""},
			update_modified=False,
		)
		return True

	def _send_to_api(self, *, commit_claim=True, allowed=SENDABLE):
		"""Send invoice data to TIMS API and handle response.

		Errors are split by whether the device may have signed the invoice:
		- nothing was sent (bad URL, connection refused, connect timeout) or the device answered
		  with a refusal: Failed, and the hourly retry sends it again;
		- the request went out but no usable answer came back (read timeout, dropped connection,
		  5xx, a body that is not JSON): Unconfirmed. It is never sent again automatically; the
		  hourly job asks TIMS whether it holds the invoice instead.

		In a background job the claim and the request log are committed together before the request,
		so a worker killed mid-request leaves Submitting behind (checked on TIMS later, once its log
		is old enough) instead of a status that would be sent again.
		"""
		if not self._claim(allowed):
			return

		missing = [item.item_code for item in self.invoice.items if not item.custom_hs_code]
		if missing:
			self._set_status(
				FAILED, _("No TIMS HS code for item(s): {0}").format(", ".join(sorted(set(missing))))
			)
			return

		endpoint = get_endpoint(self.invoice, company=self.invoice.company)
		url = f"{self.settings['api_url']}{endpoint}"
		headers = {
			"Content-Type": "application/json",
			"Authorization": f"Basic {self.settings['api_key']}",
		}
		payload = self._prepare_payload()

		if not url or not headers or not payload:
			frappe.log_error("Missing URL, headers, or payload for TIMS API.", "TimsInvoice Error")
			self._set_status(FAILED, _("Missing URL, headers, or payload for TIMS API."))
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
		if commit_claim:
			frappe.db.commit()  # nosemgrep: the claim must outlive a failed request

		try:
			response = requests.post(url, json=payload, headers=headers, timeout=(5, 30))
		except requests.exceptions.RequestException as e:
			error_msg = f"API request failed: {e!s}"
			integration_request.handle_failure(json.dumps({"error": error_msg, "response": None}))
			if request_never_left(e):
				self._log_error(error_msg)
			else:
				self._mark_unconfirmed(error_msg)
			return

		try:
			response_data = response.json()
		except ValueError:
			response_data = None

		if 400 <= response.status_code < 500:
			# The device refused the request (bad key, bad payload): nothing was signed.
			body = response_data if isinstance(response_data, dict) else {}
			error_msg = body.get("message") or f"TIMS refused the request (HTTP {response.status_code})."
			integration_request.handle_failure(json.dumps({"error": error_msg, "response": response.text}))
			self.handle_failure({**body, "message": error_msg})
			return

		if response.status_code >= 500 or not isinstance(response_data, dict):
			error_msg = f"TIMS gave no usable answer (HTTP {response.status_code})."
			integration_request.handle_failure(json.dumps({"error": error_msg, "response": response.text}))
			self._mark_unconfirmed(error_msg)
			return

		if response.ok and response_data.get("description") == "Signed successfully.":
			integration_request.handle_success(json.dumps(response_data))
			self._update_invoice(response_data)
			return

		error_msg = response_data.get("error_status", "Unknown error")
		integration_request.handle_failure(json.dumps({"error": error_msg, "response": response_data}))
		self.handle_failure(response_data)

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
				"custom_signing_status": SIGNED,
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
		self._set_status(FAILED, response_data.get("message"))

	def _log_error(self, message):
		"""Log API errors."""
		frappe.log_error("TimsInvoice Error", f"TIMS API Error: {message}")
		self._set_status(FAILED, message)

	def _mark_unconfirmed(self, message):
		"""The device may have signed the invoice: hold it for a check on TIMS, never a resend."""
		frappe.log_error("TimsInvoice Unconfirmed", f"{self.invoice.name}: {message}")
		self._set_status(
			UNCONFIRMED,
			_("Sent, but TIMS did not confirm ({0}). It will be checked on TIMS, not sent again.").format(
				message
			),
		)

	def _set_status(self, status, description):
		frappe.db.set_value(
			"Sales Invoice",
			self.invoice.name,
			{"custom_signing_status": status, "custom_tims_response_description": description},
			update_modified=False,
		)

	def check_on_tims(self):
		"""For an Unconfirmed (or stale Submitting) invoice, ask TIMS whether it holds it.

		Found with a CU invoice number: recorded as Signed. Anything else leaves it Unconfirmed with
		the device's answer, for a person to check before choosing to send it again.
		"""
		stored = frappe.db.get_value(
			"Sales Invoice",
			self.invoice.name,
			["custom_signing_status", "etr_invoice_number"],
			as_dict=True,
			for_update=True,
		)
		if stored.etr_invoice_number or stored.custom_signing_status not in (UNCONFIRMED, SUBMITTING):
			return

		result = query_tims(self.settings, self.invoice.invoice_number)
		cu_invoice_number = first_of(result, "cu_invoice_number", "cu invoice number")
		if result.get("status") == "00" and cu_invoice_number:
			verify_url = first_of(result, "verify_url", "verify url")
			frappe.db.set_value(
				"Sales Invoice",
				self.invoice.name,
				{
					"etr_invoice_number": cu_invoice_number,
					"etr_serial_number": first_of(result, "cu_serial_number", "cu serial number"),
					"custom_verify_url": verify_url,
					"custom_qr_code": get_qr_code(verify_url) if verify_url else None,
					"custom_signing_status": SIGNED,
					"custom_tims_response_description": _("Found on TIMS after an unconfirmed send."),
					"cu_invoice_date": frappe.utils.today(),
					"is_filed": 1,
				},
				update_modified=False,
			)
			return

		self._set_status(
			UNCONFIRMED,
			_(
				"TIMS does not confirm this invoice (status {0}: {1}). Check the device before sending again."
			).format(result.get("status"), result.get("description")),
		)


def request_never_left(error):
	"""True when a requests error was raised before the request reached the device."""
	if isinstance(error, NOT_SENT_ERRORS):
		return True
	if isinstance(error, requests.exceptions.ConnectionError) and error.args:
		return isinstance(getattr(error.args[0], "reason", None), NewConnectionError)
	return False


def first_of(data, *keys):
	for key in keys:
		if data.get(key):
			return data[key]
	return None


def enqueue_invoice_signing(invoice_name, company, *, enqueue_after_commit=False, allow_unconfirmed=False):
	"""Queue TIMS signing for an invoice. Shared by submit, UI, and retry paths.

	One job per invoice: a second request while one is queued or running is dropped.
	"""
	frappe.enqueue(
		SEND_INVOICE_TO_TIMS,
		enqueue_after_commit=enqueue_after_commit,
		job_id=f"tims_incotex_sign_{invoice_name}",
		deduplicate=True,
		invoice_name=invoice_name,
		company=company,
		allow_unconfirmed=allow_unconfirmed,
		queue="default",
		timeout=300,
	)


def send_invoice_to_tims(invoice_name, company, allow_unconfirmed=False):
	"""Background worker: load invoice and send it to TIMS."""
	tims = TimsInvoice(invoice_name, company)
	if not tims.can_sign():
		return
	allowed = (*SENDABLE, UNCONFIRMED) if allow_unconfirmed else SENDABLE
	tims._send_to_api(allowed=allowed)


def check_invoice_on_tims(invoice_name, company):
	"""Background worker: ask TIMS about an Unconfirmed invoice."""
	TimsInvoice(invoice_name, company).check_on_tims()


def on_submit(doc, method):
	"""Trigger invoice signing on submission."""
	if frappe.db.exists("Tims Incotex Settings", {"company": doc.company}):
		if is_active(doc.company):
			TimsInvoice(doc.name, doc.company).sign_invoice(enqueue_after_commit=True)


@frappe.whitelist()
def sign_single_invoice(invoice_name, company=None, send_unconfirmed=0):
	"""Queue signing for one invoice, for a user who may submit it.

	An Unconfirmed invoice is sent again only when the user asks for it explicitly
	(`send_unconfirmed`), after checking the device.
	"""
	invoice = frappe.get_doc("Sales Invoice", invoice_name)
	invoice.check_permission("submit")
	if is_active(invoice.company):
		enqueue_invoice_signing(
			invoice.name,
			invoice.company,
			enqueue_after_commit=True,
			allow_unconfirmed=bool(cint(send_unconfirmed)),
		)


@frappe.whitelist()
def check_single_invoice(invoice_name):
	"""Ask TIMS now about one Unconfirmed invoice."""
	invoice = frappe.get_doc("Sales Invoice", invoice_name)
	invoice.check_permission("submit")
	TimsInvoice(invoice.name, invoice.company).check_on_tims()


@frappe.whitelist()
def retry_pending_invoices(batch_size=100):
	"""Enqueue a limited batch of failed/unsigned invoices for TIMS signing, and a TIMS check for
	unconfirmed ones (never a resend).

	Avoids loading full Sales Invoice docs in the scheduler job so large
	backlogs do not hit the RQ job timeout. Eligibility is re-checked in the worker.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can retry TIMS submissions."), frappe.PermissionError)

	pending_invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"is_opening": "No",
			"custom_signing_status": ["in", [FAILED, ""]],
			"etr_invoice_number": ["in", ["", None]],
		},
		fields=["name", "company"],
		order_by="modified desc",
		limit_page_length=cint(batch_size),
	)

	active_companies = {}

	def active(company):
		if company not in active_companies:
			active_companies[company] = bool(is_active(company))
		return active_companies[company]

	for inv in pending_invoices:
		if active(inv.company):
			enqueue_invoice_signing(inv.name, inv.company)

	for inv in invoices_to_check_on_tims(cint(batch_size)):
		if active(inv.company):
			frappe.enqueue(
				CHECK_INVOICE_ON_TIMS,
				job_id=f"tims_incotex_check_{inv.name}",
				deduplicate=True,
				invoice_name=inv.name,
				company=inv.company,
				queue="default",
				timeout=300,
			)


def invoices_to_check_on_tims(limit):
	"""Unconfirmed invoices, plus Submitting ones whose send started long enough ago to be dead."""
	candidates = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"custom_signing_status": ["in", [UNCONFIRMED, SUBMITTING]],
			"etr_invoice_number": ["in", ["", None]],
		},
		fields=["name", "company", "custom_signing_status"],
		order_by="modified desc",
		limit_page_length=limit,
	)
	cutoff = add_to_date(now_datetime(), minutes=-STALE_SUBMITTING_MINUTES)
	due = []
	for inv in candidates:
		if inv.custom_signing_status == SUBMITTING:
			last_sent = frappe.db.get_value(
				"Integration Request",
				{"reference_doctype": "Sales Invoice", "reference_docname": inv.name},
				"creation",
				order_by="creation desc",
			)
			if last_sent and last_sent > cutoff:
				continue
		due.append(inv)
	return due


def query_tims(settings, invoice_number):
	"""Ask the device about one invoice, by the invoice number it was signed with."""
	url = settings.get("api_url") + settings.get("query_endpoint")
	headers = {
		"Content-Type": "application/json",
		"Authorization": f"Basic {settings['api_key']}",
	}
	payload = {
		"invoice_number": invoice_number,
		"username": settings.get("username"),
		"password": settings.get("password"),
	}

	try:
		response = requests.post(url, json=payload, headers=headers, timeout=10)
		response_data = response.json()
	except (requests.exceptions.RequestException, ValueError) as e:
		frappe.log_error(f"Failed to connect: {e!s}", "TIMS Invoice Query")
		return {
			"message": "Error",
			"status": "99",
			"description": f"Failed to connect: {e!s}",
		}

	if response_data.get("status") != "00":
		frappe.log_error(f"TIMS Query Failed: {response_data}", "TIMS Invoice Query")
	return response_data


@frappe.whitelist()
def get_invoice(invoice, company=None):
	"""Query TIMS for an invoice the user may read. `invoice` is the Sales Invoice name; TIMS is asked
	by the same `invoice_number` the invoice was signed with."""
	doc = frappe.get_doc("Sales Invoice", invoice)
	doc.check_permission("read")
	settings = get_tims_settings(doc.company)
	if not settings:
		frappe.throw("TIMS settings not configured for this company.")

	response_data = query_tims(settings, doc.invoice_number or re.sub(r"[^a-zA-Z0-9]", "", doc.name))
	if response_data.get("status") != "00":
		return {
			"message": "Query failed",
			"status": response_data.get("status", "99"),
			"description": response_data.get("description", "Unknown error"),
		}
	return response_data


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
