from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate, nowdate
from urllib3.exceptions import MaxRetryError, NewConnectionError

from tims_incotex.tims_incotex.api import sales_invoice as tims

COMPANY = "_TIMS Test Company"
ABBR = "_TTC"
CUSTOMER = "_TIMS Test Customer"
ITEM = "_TIMS Test Item"
PRICE_LIST = "_TIMS Test Selling"
HS_CODE = "0001.12.00"
SIGNED_REPLY = {
	"description": "Signed successfully.",
	"cu_serial_number": "KRAMW000000000000001",
	"cu_invoice_number": "0010000000000000001",
	"verify_url": "https://itax.kra.go.ke/KRA-Portal/invoiceChk.htm?actionCode=loadPage&invoiceNo=1",
	"message": "Signed",
}

POST = "tims_incotex.tims_incotex.api.sales_invoice.requests.post"
ENQUEUE = "tims_incotex.tims_incotex.api.sales_invoice.frappe.enqueue"


def reply(status_code=200, body=None):
	response = MagicMock()
	response.status_code = status_code
	response.ok = status_code < 400
	response.text = "" if body is None else str(body)
	if body is None:
		response.json.side_effect = ValueError("no JSON")
	else:
		response.json.return_value = body
	return response


def refused():
	reason = NewConnectionError(None, "Connection refused")
	return requests.exceptions.ConnectionError(MaxRetryError(None, "/sign", reason=reason))


def leaf(doctype, root):
	"""A non-group node under `root`: ERPNext refuses group nodes on customers and items."""
	name = f"_TIMS Test {doctype}"
	if not frappe.db.exists(doctype, name):
		parent_field = "parent_" + frappe.scrub(doctype)
		name_field = frappe.scrub(doctype) + "_name"
		frappe.get_doc({"doctype": doctype, name_field: name, parent_field: root, "is_group": 0}).insert()
	return name


def make_company():
	if not frappe.db.exists("Customer Group", "All Customer Groups"):
		from erpnext.setup.setup_wizard.operations.install_fixtures import install

		install("Kenya")
	if not frappe.db.exists("Company", COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY,
				"abbr": ABBR,
				"default_currency": "KES",
				"country": "Kenya",
				"chart_of_accounts": "Standard",
			}
		).insert()
	year = getdate(nowdate()).year
	if not frappe.db.exists("Fiscal Year", str(year)):
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": str(year),
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			}
		).insert()
	if not frappe.db.exists("Customer", CUSTOMER):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": CUSTOMER,
				"customer_group": leaf("Customer Group", "All Customer Groups"),
				"territory": leaf("Territory", "All Territories"),
			}
		).insert()
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM,
				"item_group": leaf("Item Group", "All Item Groups"),
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		).insert()
	if not frappe.db.exists("Price List", PRICE_LIST):
		frappe.get_doc(
			{"doctype": "Price List", "price_list_name": PRICE_LIST, "currency": "KES", "selling": 1}
		).insert()
	if not frappe.db.exists("Tims Incotex Settings", COMPANY):
		frappe.get_doc(
			{
				"doctype": "Tims Incotex Settings",
				"company": COMPANY,
				"company_pin": "P000000000A",
				"server_base_url": "https://tims.invalid/api/",
				"query_endpoint": "query",
				"health_check_endpoint": "health",
				"invoice_inclusive": "sign?invoice+inclusive",
				"invoice_exclusive": "sign?invoice",
				"credit_note_inclusive": "sign?credit+inclusive",
				"credit_note_exclusive": "sign?credit",
				"api_key": "test-key",
				"active": 0,
			}
		).insert()


def make_invoice(hs_code=HS_CODE):
	"""A submitted invoice that has not been sent (settings stay inactive while it is submitted)."""
	invoice = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": COMPANY,
			"customer": CUSTOMER,
			"currency": "KES",
			"conversion_rate": 1,
			"selling_price_list": PRICE_LIST,
			"price_list_currency": "KES",
			"plc_conversion_rate": 1,
			"posting_date": nowdate(),
			"items": [{"item_code": ITEM, "qty": 1, "rate": 100}],
		}
	)
	invoice.insert()
	invoice.submit()
	frappe.db.set_value(
		"Sales Invoice Item", {"parent": invoice.name}, "custom_hs_code", hs_code, update_modified=False
	)
	return invoice.name


def status(name):
	return frappe.db.get_value(
		"Sales Invoice",
		name,
		["custom_signing_status", "etr_invoice_number", "custom_tims_response_description"],
		as_dict=True,
	)


class TestTimsSigning(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_company()
		frappe.db.set_value("Tims Incotex Settings", COMPANY, "active", 1)

	def setUp(self):
		# The worker commits its claim; inside a test that would end the test transaction.
		patcher = patch.object(frappe.db, "commit")
		patcher.start()
		self.addCleanup(patcher.stop)
		frappe.db.set_value("Tims Incotex Settings", COMPANY, "active", 0)
		self.invoice = make_invoice()
		frappe.db.set_value("Tims Incotex Settings", COMPANY, "active", 1)

	def send(self, **kwargs):
		tims.send_invoice_to_tims(self.invoice, COMPANY, **kwargs)

	def test_signed_reply_records_the_cu_number(self):
		with patch(POST, return_value=reply(200, SIGNED_REPLY)) as post:
			self.send()
		self.assertEqual(post.call_count, 1)
		row = status(self.invoice)
		self.assertEqual(row.custom_signing_status, "Signed")
		self.assertEqual(row.etr_invoice_number, SIGNED_REPLY["cu_invoice_number"])

	def test_timeout_after_sending_is_unconfirmed_and_never_resent(self):
		with patch(POST, side_effect=requests.exceptions.ReadTimeout("read timed out")):
			self.send()
		self.assertEqual(status(self.invoice).custom_signing_status, "Unconfirmed")

		with patch(ENQUEUE) as enqueue:
			tims.retry_pending_invoices()
		methods = [(c.args[0], c.kwargs.get("invoice_name")) for c in enqueue.call_args_list if c.args]
		self.assertNotIn((tims.SEND_INVOICE_TO_TIMS, self.invoice), methods)
		self.assertIn((tims.CHECK_INVOICE_ON_TIMS, self.invoice), methods)

		# A plain worker run (e.g. a stale queued job) does not send it either.
		with patch(POST) as post:
			self.send()
		post.assert_not_called()

	def test_dropped_connection_and_server_errors_are_unconfirmed(self):
		for outcome in (
			{"side_effect": requests.exceptions.ConnectionError("Connection reset by peer")},
			{"return_value": reply(502, None)},
			{"return_value": reply(200, None)},
		):
			with self.subTest(outcome=outcome):
				frappe.db.set_value("Sales Invoice", self.invoice, "custom_signing_status", "")
				with patch(POST, **outcome):
					self.send()
				self.assertEqual(status(self.invoice).custom_signing_status, "Unconfirmed")

	def test_check_on_tims_records_an_invoice_the_device_holds(self):
		with patch(POST, side_effect=requests.exceptions.ReadTimeout("read timed out")):
			self.send()
		found = {"status": "00", "description": "Found", "cu invoice number": "0010000000000000002"}
		with patch(POST, return_value=reply(200, found)) as post:
			tims.check_invoice_on_tims(self.invoice, COMPANY)
		sent = post.call_args.kwargs["json"]
		self.assertEqual(
			sent["invoice_number"], frappe.db.get_value("Sales Invoice", self.invoice, "invoice_number")
		)
		row = status(self.invoice)
		self.assertEqual(row.custom_signing_status, "Signed")
		self.assertEqual(row.etr_invoice_number, "0010000000000000002")

	def test_check_on_tims_leaves_an_unknown_invoice_unconfirmed(self):
		with patch(POST, side_effect=requests.exceptions.ReadTimeout("read timed out")):
			self.send()
		with patch(POST, return_value=reply(200, {"status": "01", "description": "Not found"})):
			tims.check_invoice_on_tims(self.invoice, COMPANY)
		row = status(self.invoice)
		self.assertEqual(row.custom_signing_status, "Unconfirmed")
		self.assertIn("Not found", row.custom_tims_response_description)

	def test_explicit_resend_of_an_unconfirmed_invoice(self):
		with patch(POST, side_effect=requests.exceptions.ReadTimeout("read timed out")):
			self.send()
		with patch(POST, return_value=reply(200, SIGNED_REPLY)) as post:
			self.send(allow_unconfirmed=True)
		self.assertEqual(post.call_count, 1)
		self.assertEqual(status(self.invoice).custom_signing_status, "Signed")

	def test_request_that_never_left_is_failed_and_retried(self):
		for error in (refused(), requests.exceptions.ConnectTimeout("connect timed out")):
			with self.subTest(error=type(error).__name__):
				frappe.db.set_value("Sales Invoice", self.invoice, "custom_signing_status", "")
				with patch(POST, side_effect=error):
					self.send()
				self.assertEqual(status(self.invoice).custom_signing_status, "Failed")

		with patch(ENQUEUE) as enqueue:
			tims.retry_pending_invoices()
		sends = [c for c in enqueue.call_args_list if c.args and c.args[0] == tims.SEND_INVOICE_TO_TIMS]
		self.assertIn(self.invoice, [c.kwargs["invoice_name"] for c in sends])

	def test_device_refusal_is_failed(self):
		for refusal in (
			reply(200, {"description": "Rejected", "message": "Bad PIN"}),
			reply(401, None),
			reply(400, {"message": "Invalid payload"}),
		):
			with self.subTest(status_code=refusal.status_code):
				frappe.db.set_value("Sales Invoice", self.invoice, "custom_signing_status", "")
				with patch(POST, return_value=refusal):
					self.send()
				self.assertEqual(status(self.invoice).custom_signing_status, "Failed")

	def test_claim_is_committed_with_its_request_log(self):
		"""The hourly job reads the request log to tell a live send from a dead one, so the claim
		must never be visible without it."""
		seen = []

		def at_commit():
			seen.append(
				frappe.db.count(
					"Integration Request",
					{"reference_doctype": "Sales Invoice", "reference_docname": self.invoice},
				)
			)

		frappe.db.commit.side_effect = at_commit
		with patch(POST, return_value=reply(200, SIGNED_REPLY)):
			self.send()
		self.assertTrue(seen)
		self.assertGreaterEqual(seen[0], 1)

	def test_an_invoice_being_sent_is_not_sent_by_a_second_worker(self):
		frappe.db.set_value("Sales Invoice", self.invoice, "custom_signing_status", "Submitting")
		with patch(POST) as post:
			self.send()
		post.assert_not_called()

	def test_missing_hs_code_fails_without_sending(self):
		frappe.db.set_value(
			"Sales Invoice Item", {"parent": self.invoice}, "custom_hs_code", "", update_modified=False
		)
		with patch(POST) as post:
			self.send()
		post.assert_not_called()
		row = status(self.invoice)
		self.assertEqual(row.custom_signing_status, "Failed")
		self.assertIn(ITEM, row.custom_tims_response_description)

	def test_jobs_are_one_per_invoice(self):
		with patch(ENQUEUE) as enqueue:
			tims.enqueue_invoice_signing(self.invoice, COMPANY)
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["job_id"], f"tims_incotex_sign_{self.invoice}")
		self.assertTrue(kwargs["deduplicate"])


class TestTimsPermissions(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_company()
		email = "tims-no-roles@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": "No Roles"}).insert()
		cls.user = email

	def setUp(self):
		self.invoice = make_invoice()
		self.addCleanup(frappe.set_user, "Administrator")

	def test_a_user_who_cannot_submit_cannot_trigger_signing(self):
		frappe.set_user(self.user)
		with patch(ENQUEUE) as enqueue, self.assertRaises(frappe.PermissionError):
			tims.sign_single_invoice(self.invoice, COMPANY)
		enqueue.assert_not_called()

	def test_only_a_system_manager_can_retry_everything(self):
		frappe.set_user(self.user)
		with patch(ENQUEUE) as enqueue, self.assertRaises(frappe.PermissionError):
			tims.retry_pending_invoices()
		enqueue.assert_not_called()

	def test_a_user_who_cannot_read_the_invoice_cannot_query_it(self):
		frappe.set_user(self.user)
		with patch(POST) as post, self.assertRaises(frappe.PermissionError):
			tims.get_invoice(self.invoice, COMPANY)
		post.assert_not_called()
