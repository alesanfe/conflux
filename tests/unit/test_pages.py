# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.pages — HTML admin/configure."""
import unittest

import conflux.web.pages as pg


class TestPages(unittest.TestCase):
    def test_admin_html(self):
        h = pg._admin_page()
        self.assertIn('<', h)
        self.assertIn('html', h.lower())

    def test_configure_html(self):
        h = pg._configure_page()
        self.assertIn('<form', h)

    def test_trakt_auth_html(self):
        h = pg._trakt_auth_page()
        self.assertIn('<', h)

    def test_health_page_json(self):
        d = pg._health_page()
        self.assertIn('sources', d)

    def test_trakt_status_json(self):
        d = pg._trakt_status()
        self.assertIsInstance(d, dict)


if __name__ == '__main__':
    unittest.main()
