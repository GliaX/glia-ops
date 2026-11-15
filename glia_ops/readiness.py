"""Cutover readiness report.

A single JSON answer to "what is still missing before glia.org points here?".
Read-only: it inspects configuration and content, never changes anything.

    bench --site asset.glia.org execute glia_ops.readiness.report
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import frappe

SITE = "asset.glia.org"
MARKER = f"/home/frappe/frappe-bench/sites/{SITE}/private/backups/last_run.json"


def _check(name, ready, detail, severity="warn"):
	return {"name": name, "ready": bool(ready), "severity": "ok" if ready else severity, "detail": detail}


def _backups():
	if not os.path.exists(MARKER):
		return _check("backups", False, "no status marker: the backup job has never completed", "critical")
	try:
		state = json.load(open(MARKER, encoding="utf-8"))
	except Exception:
		return _check("backups", False, "status marker unreadable", "critical")

	if (state.get("status") or "").lower() != "ok":
		return _check(
			"backups", False, f"last run {state.get('status')} at stage {state.get('stage')}", "critical"
		)

	try:
		stamp = datetime.fromisoformat((state.get("finished") or "").replace("Z", "+00:00"))
		if stamp.tzinfo is None:
			stamp = stamp.replace(tzinfo=timezone.utc)
		age = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
	except Exception:
		return _check("backups", False, "marker has no parseable timestamp", "critical")

	return _check("backups", age <= 26, f"newest backup {state.get('stamp')} is {age:.1f} h old", "critical")


def _redirects():
	settings = frappe.get_doc("Website Settings", "Website Settings")
	sources = {(row.source or "").strip("/") for row in settings.get("route_redirects", []) if row.source}
	items = frappe.get_all("Website Item", filters={"published": 1}, fields=["route", "item_code"])
	pages = frappe.get_all("Web Page", filters={"published": 1}, fields=["route"])
	missing_products = [
		(r.route or r.item_code) for r in items if f"products/{r.route or r.item_code}" not in sources
	]
	missing_pages = [
		r.route.strip("/")
		for r in pages
		if r.route and f"pages/{r.route.strip('/')}" not in sources and not r.route.startswith("blog/")
	]
	ready = not missing_products and not missing_pages
	return _check(
		"redirects",
		ready,
		f"{len(sources)} redirects; missing {len(missing_products)} product and {len(missing_pages)} page URLs",
	)


def _tax():
	templates = frappe.db.count("Sales Taxes and Charges Template", {"company": "Glia", "disabled": 0})
	rules = frappe.db.count("Tax Rule")
	items_with_tax = frappe.db.sql(
		"""select count(distinct i.name) c from `tabItem` i
		   join `tabItem Tax` it on it.parent = i.name
		   join `tabWebsite Item` wi on wi.item_code = i.name
		   where wi.published = 1 and ifnull(it.item_tax_template, '') != ''""",
		as_dict=True,
	)[0].c
	ready = templates > 0 and (items_with_tax > 0 or rules > 0)
	return _check(
		"tax",
		ready,
		f"{templates} templates, {rules} tax rules, {items_with_tax} published items with a tax template"
		if not ready
		else f"{templates} templates, {rules} rules, {items_with_tax} items taxed",
		"critical",
	)


def _accounts():
	customers = frappe.db.count("Customer")
	users = frappe.db.count("User", {"user_type": "Website User"})
	page = frappe.get_app_path("glia_guest_checkout", "www", "claim_account.html")
	page_ok = os.path.exists(page)
	return _check(
		"accounts",
		page_ok,
		f"{users} website users for {customers} customers; claim page {'present' if page_ok else 'missing'}",
	)


def _recurring():
	enabled = frappe.db.get_single_value("Donation Settings", "enabled")
	secret = frappe.get_doc("Donation Settings").get_password("stripe_webhook_secret", raise_exception=False)
	booking = frappe.db.get_single_value("Donation Settings", "booking_enabled")
	return _check(
		"recurring donations",
		enabled and bool(secret),
		f"enabled={bool(enabled)}, webhook secret={'set' if secret else 'missing'}, booking={'on' if booking else 'off'}",
		"critical",
	)


def _one_off_donations():
	"""The Payment Entry hook must exist for one-off gifts to reach the CRM."""
	hooks = frappe.get_hooks("doc_events") or {}
	pe_hooks = (hooks.get("Payment Entry") or {}).get("on_submit") or []
	ready = any("glia_donations.donation_from_sales" in str(h) for h in pe_hooks)
	return _check("one-off donation records", ready, f"Payment Entry hooks: {pe_hooks}", "critical")


def _fulfilment():
	"""POD orders still ship out-of-band; Delivery Notes exist but tracking is manual."""
	dns = frappe.db.count("Delivery Note")
	has_tracking = frappe.get_meta("Delivery Note").has_field("tracking_number") or bool(
		frappe.get_all("Custom Field", filters={"dt": "Delivery Note", "fieldname": ["like", "%tracking%"]})
	)
	return _check(
		"fulfilment",
		has_tracking,
		f"{dns} delivery notes; tracking field {'present' if has_tracking else 'missing (manual/AWB entry)'}",
	)


def report():
	frappe.set_user("Administrator")
	try:
		checks = [
			_backups(),
			_redirects(),
			_tax(),
			_accounts(),
			_recurring(),
			_one_off_donations(),
			_fulfilment(),
		]
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Cutover readiness report failed")
		raise
	finally:
		frappe.set_user("Guest")

	blocking = [c for c in checks if not c["ready"] and c["severity"] == "critical"]
	return {
		"site": SITE,
		"ready_for_cutover": not blocking,
		"blocking": [c["name"] for c in blocking],
		"checks": checks,
	}
