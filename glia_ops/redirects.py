"""Shopify → ERPNext URL redirects.

Shopify's URL shapes differ from ERPNext's flat routes, so links in emails,
printed material, ads and search results would 404 after cutover. This generates
the 301 map from data already in the database:

    /products/<handle>       → /<handle>            (published Website Items)
    /pages/<handle>          → /<handle>            (published Web Pages)
    /blogs/<blog>/<post>     → /blog/<blog>/<post>  (published Blog Posts)
    /collections/<handle>    → /all-products        (known Shopify collection handles)
    /account[/login|/register] → /login

Idempotent: existing sources are left untouched, so it is safe to re-run after
adding products or pages.

    bench --site asset.glia.org execute glia_ops.redirects.generate_redirects
"""

from __future__ import annotations

import frappe

# Every collection handle Shopify ever exposed (from the Admin API). The new site
# has a single storefront listing, so all of them land there.
COLLECTIONS = (
	"all",
	"glia",
	"merch",
	"glia-merch",
	"frontpage",
	"featured",
	"apil",
	"glia-donations",
	"education",
	"ppe",
	"fanatics",
	"help-support-glia-campaigns",
)

# Shopify URLs whose handle does not equal the ERPNext route, plus the two
# event-admission products that are not sold on the new site.
EXTRA_REDIRECTS = (
	# Frappe truncates long routes, so the Shopify handle needs its own hop.
	(
		"blogs/press-releases/palestinian-led-team-produces-first-fully-local-external-fixator-in-gaza-using-recycled-plastics-3d-printing-and-solar-power",
		"blog/press-releases/palestinian-led-team-produces-first-fully-local-external-fixator-in-gaza-using-recycled-plastics-3d-printing-a",
	),
	("pages/contact", "contact"),
	("pages/stethoscope", "stethoscope"),
	("pages/otoscope", "otoscope"),
	("pages/dhul-hijjah-daily-giving", "dhul-hijjah-daily-giving"),
	("products/chicago_lovesongs4gaza", "chicago-lovesongs4gaza"),
	("products/event-admission", "all-products"),
	("products/event-admission-restoring-care-sumud-screening", "all-products"),
)
STATUS = "301"


def _settings():
	return frappe.get_doc("Website Settings", "Website Settings")


def _existing_sources(settings) -> set:
	return {(row.source or "").strip("/") for row in settings.get("route_redirects", []) if row.source}


def _append(settings, source: str, target: str) -> str:
	source = (source or "").strip("/")
	target = (target or "").strip("/")
	if not source or not target or source == target:
		return "skip"
	settings.append(
		"route_redirects",
		{
			"source": source,
			"target": target,
			"redirect_http_status": STATUS,
			"forward_query_parameters": 1,
		},
	)
	return "created"


def generate_redirects():
	"""Add the Shopify → ERPNext redirects to Website Settings.route_redirects."""
	stats = {"created": 0, "exists": 0, "skip": 0}
	samples = []
	settings = _settings()
	seen = _existing_sources(settings)

	def record(label, source, target):
		normalised = (source or "").strip("/")
		if normalised in seen:
			stats["exists"] += 1
			return
		result = _append(settings, source, absolute(target))
		stats[result] = stats.get(result, 0) + 1
		if result == "created":
			seen.add(normalised)
			if len(samples) < 10:
				samples.append({"kind": label, "source": normalised, "target": target})

	for row in frappe.get_all("Website Item", filters={"published": 1}, fields=["route", "item_code"]):
		handle = row.route or row.item_code
		record("product", f"products/{handle}", handle)

	for row in frappe.get_all("Web Page", filters={"published": 1}, fields=["route"]):
		handle = (row.route or "").strip("/")
		if not handle or handle.startswith("blog/"):
			continue
		record("page", f"pages/{handle}", handle)

	blog_slugs = {b.name: (b.slug or "").strip("/") for b in frappe.get_all("Blog", fields=["name", "slug"])}
	for post in frappe.get_all("Blog Post", filters={"published": 1}, fields=["route", "slug", "blog"]):
		slug = (post.slug or "").strip("/") or (post.route or "").rsplit("/", 1)[-1]
		blog = blog_slugs.get(post.blog) or ""
		if not slug or not blog:
			continue
		record("post", f"blogs/{blog}/{slug}", f"blog/{blog}/{slug}")

	for handle in COLLECTIONS:
		record("collection", f"collections/{handle}", "all-products")

	for source, target in EXTRA_REDIRECTS:
		record("override", source, target)
	record("account", "account", "login")
	record("account", "account/login", "login")
	record("account", "account/register", "login")

	settings.flags.ignore_permissions = True
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_cache()

	fresh = _settings()
	total = len(fresh.get("route_redirects", []))
	frappe.logger("glia_ops").info(f"redirect generation: {stats} (total {total})")
	return {"stats": stats, "samples": samples, "total_redirects": total}
