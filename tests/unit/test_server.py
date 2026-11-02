# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.server — handler + ROUTES."""
import unittest
from unittest.mock import MagicMock

import conflux.web.server as srv


class TestProfile(unittest.TestCase):
    def _h(self):
        return srv.H.__new__(srv.H)

    def test_perfil_valido(self):
        h = self._h()
        self.assertEqual(h._profile('/p/micfg/stream/movie/tt.json'),
                         '/stream/movie/tt.json')

    def test_perfil_invalido_no_match(self):
        h = self._h()
        self.assertEqual(h._profile('/p/../etc/passwd'), '/p/../etc/passwd')
        self.assertEqual(h._profile('/stream/x'), '/stream/x')

    def test_perfil_nombre_largo_no_match(self):
        h = self._h()
        self.assertEqual(h._profile('/p/' + 'a' * 40 + '/stream'), '/p/' + 'a' * 40 + '/stream')


class TestRoutes(unittest.TestCase):
    def test_stream_match(self):
        rx, _ = srv.ROUTES[1]
        m = rx.match('/stream/movie/tt0137523.json')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 'movie')

    def test_catalog_extra_match(self):
        rx, _ = srv.ROUTES[2]
        m = rx.match('/catalog/series/suniversal_series_search/search=dune.json')
        self.assertIsNotNone(m)

    def test_catalog_sin_extra(self):
        rx, _ = srv.ROUTES[3]
        m = rx.match('/catalog/movie/scinemeta_movie_top.json')
        self.assertIsNotNone(m)

    def test_meta_match(self):
        rx, _ = srv.ROUTES[4]
        m = rx.match('/meta/series/tt0944947.json')
        self.assertIsNotNone(m)


class TestRedir(unittest.TestCase):
    def test_redir_302_con_url(self):
        h = srv.H.__new__(srv.H)
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._redir('http://x')
        h.send_response.assert_called_with(302)

    def test_redir_404_sin_url(self):
        h = srv.H.__new__(srv.H)
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._redir(None)
        h.send_response.assert_called_with(404)


if __name__ == '__main__':
    unittest.main()
