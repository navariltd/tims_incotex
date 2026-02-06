app_name = "tims_incotex"
app_title = "Tims Incotex"
app_publisher = "Navari Limited"
app_description = "Incotex third party Tims integrator"
app_email = "support@navari.co.ke"
app_license = "agpl-3.0"

# Apps
# ------------------

required_apps = ["erpnext"]

fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            [
                "name",
                "in",
                (
                    "Sales Invoice Item-custom_hs_code",
                    "Sales Invoice-custom_invoice_number",
                ),
            ]
        ],
    },
]


# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "tims_incotex",
# 		"logo": "/assets/tims_incotex/logo.png",
# 		"title": "Tims Incotex",
# 		"route": "/tims_incotex",
# 		"has_permission": "tims_incotex.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/tims_incotex/css/tims_incotex.css"
# app_include_js = "/assets/tims_incotex/js/tims_incotex.js"

# include js, css files in header of web template
# web_include_css = "/assets/tims_incotex/css/tims_incotex.css"
# web_include_js = "/assets/tims_incotex/js/tims_incotex.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "tims_incotex/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {"Sales Invoice": "public/js/sales_invoice.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "tims_incotex/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "tims_incotex.utils.jinja_methods",
# 	"filters": "tims_incotex.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "tims_incotex.install.before_install"
# after_install = "tims_incotex.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "tims_incotex.uninstall.before_uninstall"
# after_uninstall = "tims_incotex.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "tims_incotex.utils.before_app_install"
# after_app_install = "tims_incotex.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "tims_incotex.utils.before_app_uninstall"
# after_app_uninstall = "tims_incotex.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "tims_incotex.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Sales Invoice": {
        "on_submit": "tims_incotex.tims_incotex.api.sales_invoice.on_submit",
        "before_save": "tims_incotex.tims_incotex.api.sales_invoice.before_save",
        "before_cancel": "tims_incotex.tims_incotex.api.sales_invoice.prevent_cancel_signed_invoice",
    },
    "Customer": {
        "before_save": "tims_incotex.tims_incotex.overrides.customer.before_save"
    },
}

# Scheduled Tasks
# ---------------
scheduler_events = {
    "cron": {
        "0 * * * *": [
            "tims_incotex.tims_incotex.api.sales_invoice.retry_pending_invoices"
        ],
    }
}

# Testing
# -------

# before_tests = "tims_incotex.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "tims_incotex.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "tims_incotex.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["tims_incotex.utils.before_request"]
# after_request = ["tims_incotex.utils.after_request"]

# Job Events
# ----------
# before_job = ["tims_incotex.utils.before_job"]
# after_job = ["tims_incotex.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"tims_incotex.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
