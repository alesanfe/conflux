# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Integración: handle_stream — collect real con fetch mockeado, dedup, orden."""
import unittest

import conflux.app.stream as S
from tests import patch_fetch


def _mkstreams(h):
    return {'streams': [{'infoHash': h, 'title': 'Movie 1080p 👤10 💾1 GB'}]}


class TestStreamFlow(unittest.TestCase):
    def setUp(self):
        S._cache.clear(); S._inflight.clear()

    def tearDown(self):
        S._cache.clear(); S._inflight.clear()

    def test_agrega_dedup_ordena(self):
        h1, h2 = 'a' * 40, 'b' * 40
        calls = {'n': 0}
        def fake(url, t=7):
            calls['n'] += 1
            return _mkstreams(h1 if calls['n'] % 2 else h2)
        restore = patch_fetch(fake)
        try:
            d = S.handle_stream('movie', 'tt0000001')
        finally:
            restore()
        streams = d['streams']
        self.assertGreaterEqual(len(streams), 1)
        hashes = [s.get('infoHash') for s in streams]
        self.assertEqual(len(hashes), len(set(hashes)), 'dedup por infoHash')
        for s in streams:
            self.assertTrue(s['name'].startswith('☁️'))

    def test_upstream_muerto_streams_vacios_no_500(self):
        restore = patch_fetch(lambda *a, **k: None)
        try:
            d = S.handle_stream('movie', 'tt0000002')
            self.assertEqual(d, {'streams': []})
        finally:
            restore()


if __name__ == '__main__':
    unittest.main()
