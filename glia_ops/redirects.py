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

COLLECTIONS = ("all", "glia", "merch", "devices", "donation", "donations", "frontpage")
STATUS = "301"


def _upsert(source: str, target: str) -> str:
	source = (source or "").strip("/")
	target = (target or "").strip("/")
	if not source or not target or source == target:
		return "skip"
	if frappe.db.exists("Website Route Redirect", {"source": source}):
		return "exists"

	doc = frappe.new_doc("Website Route Redirect")
	doc.update(
		{
			"source": source,
			"target": target,
			"redirect_http_status": STATUS,
			"forward_query_parameters": 1,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return "created"


def generate_redirects():
	stats = {"created": 0, "exists": 0, "skip": 0}
	samples = []

	def record(label, source, target):
		result = _upsert(source, target)
		stats[result] = stats.get(result, 0) + 1
		if result == "created" and len(samples) < 10:
			samples.append({"kind": label, "source": source, "target": target})

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
	record("account", "account", "login")
	record("account", "account/login", "login")
	record("account", "account/register", "login")

	frappe.db.commit()
	frappe.clear_cache()
	frappe.logger("glia_ops").info(f"redirect generation: {stats}")
	return {
		"stats": stats,
		"samples": samples,
		"total_redirects": frappe.db.count("Website Route Redirect"),
	}
