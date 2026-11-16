"""Cutover readiness report.

A single JSON answer to "what is still missing before glia.org points here?".
Read-only: it inspects configuration and content, never changes anything.

    bench --site asset.glia.org execute glia_ops.readiness.report
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):  # noqa: F821
	"""urllib follows redirects by default, which hides both the status and the hop
	count — the exact blind spot that made a looping redirect pass as healthy."""

	def redirect_request(self, req, fp, code, msg, headers, newurl):
		return None


def _follow(target: str) -> tuple[int, int]:
	"""Return (final status, hops) for a path on this site, following redirects manually."""
	import urllib.error
	import urllib.request

	base = "https://" + (frappe.local.site or SITE)
	url = base + "/" + target.lstrip("/")
	opener = urllib.request.build_opener(_NoRedirect)
	hops = 0
	for _ in range(6):
		req = urllib.request.Request(url, headers={"User-Agent": "glia-readiness/1.0"})
		try:
			with opener.open(req, timeout=8) as resp:
				return resp.status, hops
		except urllib.error.HTTPError as exc:
			if exc.code in (301, 302, 307, 308) and exc.headers.get("Location"):
				location = exc.headers["Location"]
				url = location if location.startswith("http") else base + "/" + location.lstrip("/")
				hops += 1
				continue
			return exc.code, hops
		except Exception:
			return 0, hops
	return 0, hops


def _redirects():
	settings = frappe.get_doc("Website Settings", "Website Settings")
	rows = [(row.source or "").strip("/") for row in settings.get("route_redirects", []) if row.source]
	sources = set(rows)
	settings = frappe.get_doc("Website Settings", "Website Settings")
	sources = {(row.source or "").strip("/") for row in settings.get("route_redirects", []) if row.source}
	items = frappe.get_all("Website Item", filters={"published": 1}, fields=["route", "item_code"])
	pages = frappe.get_all("Web Page", filters={"published": 1}, fields=["route"])
	missing_products = [
		(r.route or r.item_code) for r in items if f"products/{r.route or r.item_code}" not in sources
	]
	# Pages that exist only on the new site (created after the migration) need no
	# Shopify-style `pages/<handle>` redirect; requiring one would report a false gap.
	NEW_PAGES = {"privacy-policy"}
	missing_pages = [
		r.route.strip("/")
		for r in pages
		if r.route
		and f"pages/{r.route.strip('/')}" not in sources
		and not r.route.startswith("blog/")
		and r.route.strip("/") not in NEW_PAGES
	]
	# Presence is not enough: a relative target makes Frappe redirect to itself,
	# so a page can "have a redirect" and still loop. Follow a sample to the end.
	sample = [s for s in rows if s.startswith(("products/", "pages/", "collections/", "blogs/"))][:12]
	broken = []
	for source in sample:
		status, hops = _follow(source)
		if status != 200 or hops == 0 or hops > 3:
			broken.append({"source": source, "status": status, "hops": hops})

	ready = not missing_products and not missing_pages and not broken
	detail = (
		f"{len(sources)} redirects; missing {len(missing_products)} product and "
		f"{len(missing_pages)} page URLs"
	)
	if broken:
		detail += f"; {len(broken)} sampled redirects do not land on 200: " + ", ".join(
			f"{b['source']} -> {b['status']} ({b['hops']} hops)" for b in broken[:4]
		)
	return _check("redirects", ready, detail)


def _tax():
	"""Tax is applied at document level by destination province, so readiness checks
	the rate templates and the destination rules — not item-level templates, which
	this shop deliberately does not use (see glia_guest_checkout.taxes)."""
	EXPECTED = {
		"Canada HST 13% - Glia": 13.0,       # Ontario
		"Canada HST 14% - Glia": 14.0,       # Nova Scotia
		"Canada HST 15% - Glia": 15.0,       # NB / NL / PE
		"Canada GST 5% - Glia": 5.0,         # AB / NT / NU / YT
		"Canada GST+PST 12% - Glia": 12.0,   # British Columbia
		"Canada GST+RST 12% - Glia": 12.0,   # Manitoba
		"Canada GST+PST 11% - Glia": 11.0,   # Saskatchewan
		"Canada GST+QST 14.975% - Glia": 14.975,  # Quebec
	}
	missing = [name for name in EXPECTED if not frappe.db.exists("Sales Taxes and Charges Template", name)]
	rules = frappe.db.count("Tax Rule", {"tax_type": "Sales"})
	provinces = frappe.get_all("Tax Rule", filters={"tax_type": "Sales"}, pluck="shipping_state")
	provinces = {p for p in provinces if p}
	tax_id = frappe.db.get_value("Company", "Glia", "tax_id")
	ready = not missing and len(provinces) >= 13 and bool(tax_id)
	detail = f"{len(EXPECTED) - len(missing)}/{len(EXPECTED)} rate templates, {len(provinces)} province rules, BN {tax_id or 'missing'}"
	if missing:
		detail += "; missing: " + ", ".join(missing[:4])
	return _check("tax", ready, detail, "critical")


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
