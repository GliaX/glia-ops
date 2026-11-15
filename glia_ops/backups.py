"""Backup health check: alert when the backup CronJob stops delivering.

The backup CronJob (helm-erpnext/backup/) writes
`sites/<site>/private/backups/last_run.json` on every run: status ok/failed, the
stage it reached, the backup stamp and an ISO finish time. Nothing watched that
file, so a silent backup failure would only be noticed when a restore was
needed.

This module runs on the Frappe scheduler (see hooks.py) and emails
info@glia.org when the marker is missing, reports a failure, or is older than
`MAX_AGE_HOURS`. Alerts are de-duplicated for 12 h so a persistent problem does
not spam the inbox.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import frappe

SITE = "asset.glia.org"
MARKER = f"/home/frappe/frappe-bench/sites/{SITE}/private/backups/last_run.json"
MAX_AGE_HOURS = 26  # schedule is every 6h; this tolerates three missed runs
ALERT_TO = ("info@glia.org",)
DEDUPE_KEY = "glia:backup_health_alert"
DEDUPE_SECONDS = 12 * 3600


def _alert(subject: str, detail: str):
	"""Send at most one alert per dedupe window; always log."""
	frappe.log_error(detail, subject)
	if frappe.cache().get_value(DEDUPE_KEY):
		return "alert suppressed (already sent within 12h)"
	frappe.cache().set_value(DEDUPE_KEY, 1, expires_in_sec=DEDUPE_SECONDS)
	frappe.sendmail(
		recipients=ALERT_TO,
		subject=subject,
		message=(
			f"<p>{detail}</p>"
			"<p>Check: <code>kubectl -n erpnext get jobs | grep erpnext-backup</code><br>"
			"Runbook: <code>helm-erpnext/backup/README.md</code></p>"
		),
		reference_doctype="Error Log",
		now=True,
	)
	return "alert sent"


def read_marker():
	if not os.path.exists(MARKER):
		return None
	try:
		with open(MARKER, encoding="utf-8") as handle:
			return json.load(handle)
	except Exception:
		return {"status": "unreadable"}


def check():
	"""Return a dict describing the current backup health; alert when unhealthy."""
	frappe.set_user("Administrator")
	try:
		state = read_marker()
		if state is None:
			return {
				"healthy": False,
				"reason": "no marker",
				"action": _alert(
					"[Glia] ERPNext backups: no backup has run",
					f"No status marker at <code>{MARKER}</code> — no backup has completed "
					"since the CronJob was installed, or the sites volume was replaced.",
				),
			}

		status = (state.get("status") or "").lower()
		if status != "ok":
			return {
				"healthy": False,
				"reason": f"status={status or 'unknown'} stage={state.get('stage')}",
				"action": _alert(
					"[Glia] ERPNext backup FAILED",
					f"Last backup status: <b>{status or 'unknown'}</b>, stage: "
					f"<b>{state.get('stage') or '?'}</b>, at {state.get('finished') or '?'}.",
				),
			}

		finished = state.get("finished") or ""
		try:
			stamp = datetime.fromisoformat(finished.replace("Z", "+00:00"))
			if stamp.tzinfo is None:
				stamp = stamp.replace(tzinfo=timezone.utc)
			age_hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600.0
		except Exception:
			return {
				"healthy": False,
				"reason": "unparseable timestamp",
				"action": _alert(
					"[Glia] ERPNext backup status unreadable",
					f"The marker at <code>{MARKER}</code> has no parseable timestamp.",
				),
			}

		if age_hours > MAX_AGE_HOURS:
			return {
				"healthy": False,
				"reason": f"stale {age_hours:.1f}h",
				"action": _alert(
					"[Glia] ERPNext backup is stale",
					f"The newest backup ({state.get('stamp')}) finished {age_hours:.1f} h ago; "
					f"expected within {MAX_AGE_HOURS} h.",
				),
			}

		# Healthy again: clear the dedupe so a future failure alerts immediately.
		frappe.cache().delete_value(DEDUPE_KEY)
		return {
			"healthy": True,
			"stamp": state.get("stamp"),
			"age_hours": round(age_hours, 2),
			"action": "none",
		}
	finally:
		frappe.set_user("Guest")


@frappe.whitelist()
def run_check():
	frappe.only_for(("System Manager",))
	return check()


def daily_check():
	try:
		result = check()
		frappe.logger("glia_ops").info(f"backup health: {result}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Backup health check crashed")
