"""Redirect targets must be root-absolute (a relative one loops under nested paths)."""

import unittest

from glia_ops.redirects import absolute


class TestAbsolute(unittest.TestCase):
	def test_prepends_slash(self):
		self.assertEqual(absolute("stethoscope"), "/stethoscope")
		self.assertEqual(absolute("blog/news/x"), "/blog/news/x")

	def test_keeps_absolute(self):
		self.assertEqual(absolute("/all-products"), "/all-products")

	def test_keeps_external_and_scheme_urls(self):
		self.assertEqual(absolute("https://example.com/x"), "https://example.com/x")
		self.assertEqual(absolute("mailto:info@glia.org"), "mailto:info@glia.org")

	def test_empty(self):
		self.assertEqual(absolute(""), "")
		self.assertEqual(absolute(None), None)
