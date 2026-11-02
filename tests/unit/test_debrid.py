# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.debrid — providers + resolve + mark."""
import unittest
from unittest.mock import patch

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



from tests import fake_session


class _DbBase(unittest.TestCase):
    """setUp común: CFG limpio + _DB_OK limpio por test."""
    def setUp(self):
        self._old = dict(st.CFG)
        db._DB_OK.clear()
        st.CFG['debrid_provider'] = 'p'
        st.CFG['debrid_key'] = 'k'

    def tearDown(self):
        st.CFG.clear(); st.CFG.update(self._old)
        db._DB_OK.clear()


class TestDbCheck(_DbBase):
    H = 'a' * 40

    def test_dbc_rd(self):
        s, calls = fake_session({'json': {self.H: {'rd': [{}]}}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_rd('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])
        self.assertIn('instantAvailability', calls[0][1])

    def test_dbc_tb(self):
        s, _ = fake_session({'json': {'data': {self.H: True}}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_tb('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_dbc_ad(self):
        s, _ = fake_session({'json': {'data': {'magnets': [
            {'magnet': f'magnet:?xt=urn:btih:{self.H}', 'instant': True}]}}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_ad('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_dbc_pm(self):
        s, _ = fake_session({'json': {'response': [True]}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_pm('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_dbc_ed(self):
        s, _ = fake_session({'json': [
            {'url': f'magnet:?xt=urn:btih:{self.H}', 'cached': True}]})
        with patch.object(db, '_session', return_value=s):
            db._dbc_ed('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_dbc_dl(self):
        s, _ = fake_session({'json': {'value': True}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_dl('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_db_avail_cache(self):
        db._DB_OK[self.H] = True
        st.CFG['debrid_provider'] = 'real-debrid'
        self.assertEqual(db._db_avail([self.H]), {self.H})

    def test_db_avail_provider_desconocido(self):
        st.CFG['debrid_provider'] = 'zzz'
        self.assertEqual(db._db_avail([self.H]), set())


class TestDebridMark(_DbBase):
    H = 'b' * 40

    def test_marca_cached_streams(self):
        st.CFG['debrid_provider'] = 'real-debrid'
        s, _ = fake_session({'json': {self.H: {'rd': [{}]}}})
        streams = [{'infoHash': self.H, 'name': 'X', 'fileIdx': 1},
                   {'infoHash': 'c' * 40, 'name': 'Y'}]
        with patch.object(db, '_session', return_value=s):
            db._debrid_mark(streams)
        self.assertTrue(streams[0]['cached'])
        self.assertTrue(streams[0]['name'].startswith('⚡'))
        self.assertIn('/r/real-debrid/', streams[0]['url'])
        self.assertNotIn('cached', streams[1])

    def test_sin_provider_noop(self):
        st.CFG.pop('debrid_provider')
        streams = [{'infoHash': self.H}]
        db._debrid_mark(streams)
        self.assertNotIn('cached', streams[0])

    def test_sin_hashes_noop(self):
        db._debrid_mark([{'url': 'x'}])   # no debe llamar a _session


class TestDbResolvers(_DbBase):
    H = 'd' * 40

    def test_db_rd(self):
        s, calls = fake_session(
            {'json': {'id': 't1'}},
            {'json': {'files': [{'id': 1, 'name': 'a.txt'},
                                {'id': 2, 'name': 'v.mkv', 'bytes': 99}]}},
            {'json': {}},
            {'json': {'links': ['L1']}},
            {'json': {'download': 'https://rd/dl'}})
        with patch.object(db, '_session', return_value=s):
            out = db._db_rd('k', self.H, 0)
        self.assertEqual(out, 'https://rd/dl')
        self.assertIn('addMagnet', calls[0][1])

    def test_db_tb(self):
        s, _ = fake_session(
            {'json': {'data': {'torrent_id': 9}}},
            {'json': {'data': {'link': 'https://tb/dl'}}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_tb('k', self.H, 0), 'https://tb/dl')

    def test_db_ad(self):
        s, _ = fake_session(
            {'json': {'data': {'magnets': [{'id': 'm1'}]}}},
            {'json': {'data': {'magnets': {'links': [
                {'filename': 'v.mkv', 'link': 'L'}]}}}},
            {'json': {'data': {'link': 'https://ad/dl'}}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_ad('k', self.H, 0), 'https://ad/dl')

    def test_db_ad_sin_video_none(self):
        s, _ = fake_session(
            {'json': {'data': {'magnets': [{'id': 'm1'}]}}},
            {'json': {'data': {'magnets': {'links': [
                {'filename': 'a.txt', 'link': 'L'}]}}}})
        with patch.object(db, '_session', return_value=s):
            self.assertIsNone(db._db_ad('k', self.H, 0))

    def test_db_pm(self):
        s, _ = fake_session({'json': {'content': [
            {'path': 'a/b.mkv', 'link': 'PL', 'size': 5}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_pm('k', self.H, 0), 'PL')

    def test_db_pm_sin_video_none(self):
        s, _ = fake_session({'json': {'content': [{'path': 'a.txt'}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertIsNone(db._db_pm('k', self.H, 0))

    def test_db_ed(self):
        s, _ = fake_session({'json': {'files': [
            {'filename': 'v.mkv', 'url': 'EU'}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_ed('k', self.H, 0), 'EU')

    def test_db_ed_fallback_link(self):
        s, _ = fake_session({'json': {'link': 'EL'}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_ed('k', self.H, 0), 'EL')

    def test_db_dl(self):
        s, _ = fake_session(
            {'json': {'value': {'id': 'i1'}}},
            {'json': {'value': [{'name': 'v.mkv', 'downloadUrl': 'DU'}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_dl('k', self.H, 0), 'DU')

    def test_resolve_captura_excepcion(self):
        """Un provider que lanza -> None, nunca crash (boundary)."""
        st.CFG['debrid_provider'] = 'real-debrid'
        s, _ = fake_session(RuntimeError('api caída'))
        with patch.object(db, '_session', return_value=s):
            self.assertIsNone(db._db_resolve('real-debrid', self.H, 0))
