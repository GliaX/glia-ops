"""Small public API used by the theme (article sidebar).

The blog article template is Frappe's own, so the sidebar is rendered client-side
from this data rather than by overriding a core template.
"""

from __future__ import annotations

import frappe


@frappe.whitelist(allow_guest=True)
def related_posts(route=None, blog=None, limit=4):
	"""Published posts in the same blog, newest first, excluding the current one."""
	try:
		limit = max(1, min(int(limit or 4), 8))
	except (TypeError, ValueError):
		limit = 4

	filters = {"published": 1}
	if blog:
		filters["blog"] = blog

	posts = frappe.get_all(
		"Blog Post",
		filters=filters,
		fields=["title", "route", "published_on", "blog", "meta_image"],
		order_by="published_on desc",
		limit=limit + 4,
	)

	current = (route or "").strip("/")
	out = []
	for post in posts:
		target = (post.get("route") or "").strip("/")
		if current and (target == current or target.endswith(current)):
			continue
		out.append(
			{
				"title": post.get("title"),
				"route": post.get("route"),
				"date": str(post.get("published_on") or "")[:10],
				"image": post.get("meta_image"),
			}
		)
		if len(out) >= limit:
			break
	return {"posts": out}
