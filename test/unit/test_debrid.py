"""Unit tests conflux.infra.debrid — providers + resolve + mark."""
import unittest

import conflux.core.state as st
import conflux.infra.debrid as db


class TestDbCfg(unittest.TestCase):
    def setUp(self):
        self._old = dict(st.CFG)

    def tearDown(self):
        st.CFG.clear(); st.CFG.update(self._old)

    def test_sin_config_none(self):
        st.CFG.pop('debrid_provider', None); st.CFG.pop('debrid_key', None)
        self.assertEqual(db._db_cfg(), (None, None))


class TestPickFile(unittest.TestCase):
    def test_indice_valido(self):
        self.assertEqual(db._pick_file(['a', 'b', 'c'], 1), 'b')

    def test_fuera_rango_default(self):
        self.assertIsNone(db._pick_file(['a'], 5))
        self.assertEqual(db._pick_file(['a'], 5, 'd'), 'd')


class TestMarkOne(unittest.TestCase):
    def test_marca_y_reescribe(self):
        s = {'infoHash': 'h', 'name': 'n', 'sources': ['x'], 'fileIdx': 2}
        db._db_mark_one('rd', s)
        self.assertTrue(s['cached'])
        self.assertTrue(s['name'].startswith('⚡'))
        self.assertIn('/r/rd/h?fi=2', s['url'])
        self.assertNotIn('infoHash', s)
        self.assertNotIn('sources', s)


class TestResolve(unittest.TestCase):
    def setUp(self):
        self._old = dict(st.CFG)

    def tearDown(self):
        st.CFG.clear(); st.CFG.update(self._old)

    def test_sin_key_none(self):
        self.assertIsNone(db._db_resolve('rd', 'h', 0))

    def test_provider_desconocido_none(self):
        st.CFG['debrid_provider'] = 'noexiste'
        st.CFG['debrid_key'] = 'k'
        self.assertIsNone(db._db_resolve('noexiste', 'h', 0))


class TestVideoLinks(unittest.TestCase):
    def test_filtra_videos(self):
        links = [{'filename': 'a.mp4', 'link': 'u1'},
                 {'filename': 'b.txt', 'link': 'u2'},
                 {'filename': 'c.mkv', 'link': 'u3'}]
        out = db._video_links(links)
        self.assertEqual(len(out), 2)


if __name__ == '__main__':
    unittest.main()
