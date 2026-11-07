# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.debrid — providers + resolve + mark."""
import unittest
from unittest.mock import patch

import conflux.core.state as st
import conflux.infra.debrid as db


class TestPickFile(unittest.TestCase):
    def test_indice_valido(self):
        self.assertEqual(db._pick_file(['a', 'b', 'c'], 1), 'b')

    def test_fuera_rango_default(self):
        self.assertIsNone(db._pick_file(['a'], 5))
        self.assertEqual(db._pick_file(['a'], 5, 'd'), 'd')

    def test_indice_negativo_no_envuelve(self):
        """fi=-1 no debe servir fs[-1] (último fichero): cae al default —
        un ?fi=-1 en /r/ pedía silenciosamente el fichero equivocado."""
        self.assertIsNone(db._pick_file(['a', 'b'], -1))
        self.assertEqual(db._pick_file(['a', 'b'], -1, 'd'), 'd')


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

    def test_key_vacia_no_llama_fn(self):
        """key='' con provider válido -> None sin llamar al resolver
        (mutante `not k or not fn`->and procedía con key vacía)."""
        st.CFG['debrid_provider'] = 'real-debrid'
        st.CFG['debrid_key'] = ''
        m = unittest.mock.Mock(return_value='http://dl/x')
        with unittest.mock.patch.dict(db._DB_RESOLVE, {'real-debrid': m}):
            self.assertIsNone(db._db_resolve('real-debrid', 'h', 0))
        m.assert_not_called()


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

    def test_db_rd_fi_igual_len_max(self):
        """fi == len(fs) -> fuera de rango: cae al mayor vídeo (< no <=)."""
        s, _ = fake_session(
            {'json': {'id': 't1'}},
            {'json': {'files': [{'id': 2, 'name': 'v.mkv', 'bytes': 99}]}},
            {'json': {}},
            {'json': {'links': ['L1']}},
            {'json': {'download': 'https://rd/dl'}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_rd('k', self.H, 1), 'https://rd/dl')

    def test_db_rd_segundo_video_link_correcto(self):
        """fi=1 con 2 vídeos: unrestrict recibe links[1] (fs.index), no [0]
        (mutantes Or/In@252 caían a links[0])."""
        s, calls = fake_session(
            {'json': {'id': 't1'}},
            {'json': {'files': [{'id': 1, 'name': 'a.mkv', 'bytes': 1},
                                {'id': 2, 'name': 'b.mkv', 'bytes': 2}]}},
            {'json': {}},
            {'json': {'links': ['L0', 'L2']}},
            {'json': {'download': 'https://rd/dl2'}})
        with patch.object(db, '_session', return_value=s):
            out = db._db_rd('k', self.H, 1)
        self.assertEqual(out, 'https://rd/dl2')
        self.assertEqual(calls[4][2]['data']['link'], 'L2')   # unrestrict/link

    def test_db_tb(self):
        s, calls = fake_session(
            {'json': {'data': {'torrent_id': 9}}},
            {'json': {'data': {'link': 'https://tb/dl'}}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_tb('k', self.H, 0), 'https://tb/dl')
        self.assertEqual(calls[1][2]['params']['torrent_id'], 9)   # Or@263

    def test_db_tb_data_string_directa(self):
        """data como string URL (sin .link): el fallback `or data` debía
        devolverla — BUG: `(data or {}).get('link')` crasheaba con
        AttributeError antes de llegar al fallback."""
        s, _ = fake_session(
            {'json': {'data': {'torrent_id': 9}}},
            {'json': {'data': 'https://tb/direct'}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_tb('k', self.H, 0), 'https://tb/direct')

    def test_db_ad(self):
        s, calls = fake_session(
            {'json': {'data': {'magnets': [{'id': 'm1'}]}}},
            {'json': {'data': {'magnets': {'links': [
                {'filename': 'v.mkv', 'link': 'L'}]}}}},
            {'json': {'data': {'link': 'https://ad/dl'}}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_ad('k', self.H, 0), 'https://ad/dl')
        self.assertEqual(calls[1][2]['params']['id'], 'm1')       # Or@276

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

    def test_db_pm_fi_igual_len_max(self):
        """fi == len(fs) -> max por size, no IndexError (Lt@299)."""
        s, _ = fake_session({'json': {'content': [
            {'path': 'a/b.mkv', 'link': 'PL', 'size': 5}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_pm('k', self.H, 1), 'PL')

    def test_db_ed_fi_igual_len_fallback(self):
        """fi == len(files) -> files[0] como default (Lt@310)."""
        s, _ = fake_session({'json': {'files': [
            {'filename': 'v.mkv', 'url': 'EU'}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_ed('k', self.H, 1), 'EU')

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
        s, calls = fake_session(
            {'json': {'value': {'id': 'i1'}}},
            {'json': {'value': [{'name': 'v.mkv', 'downloadUrl': 'DU'}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertEqual(db._db_dl('k', self.H, 0), 'DU')
        self.assertTrue(calls[0][2]['json']['async'])            # True@320
        self.assertIn('/i1/', calls[1][1])                       # Or@322

    def test_db_oc_en_cola_con_videos_none(self):
        """status!=downloaded -> None aunque explore tenga vídeos (Or@336:
        el `or` del guarda también corta con requestId presente)."""
        s, _ = fake_session(
            {'json': {'requestId': 'r1', 'status': 'created',
                      'url': 'https://dl01.offcloud.com/cloud/download/r1'}},
            {'json': [{'url': 'dir/f.mkv'}]})
        with patch.object(db, '_session', return_value=s):
            self.assertIsNone(db._db_oc('k', self.H, 0))

    def test_db_oc(self):
        """Offcloud: cloud(add magnet) downloaded -> explore -> mayor video."""
        s, _ = fake_session(
            {'json': {'requestId': 'r1', 'status': 'downloaded',
                      'url': 'https://dl01.offcloud.com/cloud/download/r1'}},
            {'json': [{'url': 'dir/f.mkv'}, {'url': 'dir/nfo.txt'}]})
        with patch.object(db, '_session', return_value=s):
            out = db._db_oc('k', self.H, 0)
        self.assertEqual(out, 'https://dl01.offcloud.com/cloud/download/r1/dir/f.mkv')

    def test_db_oc_en_cola_none(self):
        """Status != downloaded (cola/descargando): no se sirve aún."""
        s, _ = fake_session({'json': {'requestId': 'r1', 'status': 'created'}})
        with patch.object(db, '_session', return_value=s):
            self.assertIsNone(db._db_oc('k', self.H, 0))

    def test_db_pio(self):
        s, calls = fake_session(
            {'json': {'transfer': {'id': 9, 'file_id': 100}}},
            {'json': {'files': [{'id': 7, 'name': 'v.mkv', 'size': 9},
                                {'id': 8, 'name': 'n.txt', 'size': 1}]}})
        with patch.object(db, '_session', return_value=s):
            out = db._db_pio('k', self.H, 0)
        self.assertEqual(out, 'https://api.put.io/v2/files/7/download?oauth_token=k')
        q = calls[1][2]['params']
        self.assertEqual((q['parent_id'], q['per_page']), (100, 200))  # Or/200@367

    def test_db_pio_sin_video_none(self):
        s, _ = fake_session(
            {'json': {'transfer': {'id': 9, 'file_id': 100}}},
            {'json': {'files': [{'id': 8, 'name': 'n.txt'}]}})
        with patch.object(db, '_session', return_value=s):
            self.assertIsNone(db._db_pio('k', self.H, 0))

    def test_dbc_oc(self):
        s, _ = fake_session({'json': {
            f'magnet:?xt=urn:btih:{self.H}': {'status': 'cached'}}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_oc('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_dbc_pio(self):
        """Put.io: cached = ya está en transfers COMPLETED/SEEDING de la cuenta."""
        s, _ = fake_session({'json': {'transfers': [
            {'source': f'magnet:?xt=urn:btih:{self.H}', 'status': 'COMPLETED'}]}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_pio('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_dbc_pio_descargando_no_cached(self):
        s, _ = fake_session({'json': {'transfers': [
            {'source': f'magnet:?xt=urn:btih:{self.H}', 'status': 'DOWNLOADING'}]}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_pio('k', [self.H])
        self.assertFalse(db._DB_OK[self.H])

    def test_resolve_captura_excepcion(self):
        """Un provider que lanza -> None, nunca crash (boundary)."""
        st.CFG['debrid_provider'] = 'real-debrid'
        s, _ = fake_session(RuntimeError('api caída'))
        with patch.object(db, '_session', return_value=s):
            self.is_none = db._db_resolve('real-debrid', self.H, 0)
            self.assertIsNone(self.is_none)


class TestUsenet(_DbBase):
    """_db_usenet — torbox createusenetdownload -> mylist -> requestdl."""

    def test_sin_torbox_none(self):
        st.CFG.clear()
        self.assertIsNone(db._db_usenet('https://x/y.nzb'))

    def test_flujo_completo(self):
        st.CFG['debrid_provider'] = 'torbox'
        s, calls = fake_session(
            {'json': {'data': {'usenetdownload_id': 7}}},
            {'json': {'data': [{'usenetdownload_id': 7, 'files': [
                {'id': 3, 'name': 'v.mkv', 'size': 9}]}]}},
            {'json': {'data': {'link': 'https://tb/nzb'}}})
        with patch.object(db, '_session', return_value=s):
            out = db._db_usenet('https://x/y.nzb')
        self.assertEqual(out, 'https://tb/nzb')
        q = calls[2][2]['params']
        self.assertEqual(q['usenet_id'], 7)
        self.assertEqual(q['file_id'], 3)              # vídeo mayor elegido

    def test_fsel_solo_videos_no_otros(self):
        """mylist con txt más grande que el mkv: file_id sigue siendo el
        vídeo (mutante Or@420 elegía del listado SIN filtrar)."""
        st.CFG['debrid_provider'] = 'torbox'
        s, calls = fake_session(
            {'json': {'data': {'usenetdownload_id': 7}}},
            {'json': {'data': [{'usenetdownload_id': 7, 'files': [
                {'id': 3, 'name': 'v.mkv', 'size': 9},
                {'id': 8, 'name': 'big.txt', 'size': 999}]}]}},
            {'json': {'data': {'link': 'https://tb/nzb'}}})
        with patch.object(db, '_session', return_value=s):
            db._db_usenet('https://x/y.nzb')
        self.assertEqual(calls[2][2]['params']['file_id'], 3)

    def test_mylist_otro_item_no_id(self):
        """El item se busca por usenetdownload_id==uid (mutante Eq@416)."""
        st.CFG['debrid_provider'] = 'torbox'
        s, calls = fake_session(
            {'json': {'data': {'usenetdownload_id': 7}}},
            {'json': {'data': [{'usenetdownload_id': 99, 'files': [
                {'id': 1, 'name': 'otro.mkv', 'size': 1}]}]}},
            {'json': {'data': {'link': 'https://tb/nzb'}}})
        with patch.object(db, '_session', return_value=s):
            db._db_usenet('https://x/y.nzb')
        self.assertNotIn('file_id', calls[2][2]['params'])  # uid 7 no está

    def test_sin_uid_none(self):
        st.CFG['debrid_provider'] = 'torbox'
        s, _ = fake_session({'json': {'data': {}}})
        with patch.object(db, '_session', return_value=s):
            self.assertIsNone(db._db_usenet('https://x/y.nzb'))


class TestDbStream(_DbBase):
    """_db_stream — /dp/: resuelve y re-abre con headers de validación."""

    def test_reenvia_solo_headers_validacion(self):
        """Range/If-* se reenvían; el resto no (mutante In@441)."""
        st.CFG['debrid_provider'] = 'real-debrid'
        s, calls = fake_session({'json': {}})
        with patch.object(db, '_db_resolve', return_value='https://cdn.x/f'), \
                patch.object(db, '_session', return_value=s):
            db._db_stream('real-debrid', 'h', 0,
                          {'Range': 'bytes=0-1', 'If-None-Match': 'e',
                           'User-Agent': 'u', 'Cookie': 'secret'})
        fwd = calls[0][2]['headers']
        self.assertEqual(fwd, {'Range': 'bytes=0-1', 'If-None-Match': 'e'})
        self.assertTrue(calls[0][2]['stream'])           # stream=True (True@442)

    def test_session_por_host_resuelto(self):
        st.CFG['debrid_provider'] = 'real-debrid'
        s, _ = fake_session({'json': {}})
        with patch.object(db, '_db_resolve', return_value='https://cdn.ej.com/f'), \
                patch.object(db, '_session', return_value=s) as m:
            db._db_stream('real-debrid', 'h', 0, {})
        m.assert_called_once_with('cdn.ej.com')          # no 'debrid' (Or@439)

    def test_sin_url_none(self):
        with patch.object(db, '_db_resolve', return_value=None):
            self.assertIsNone(db._db_stream('real-debrid', 'h', 0, {}))

    def test_url_no_publica_rechazada(self):
        """SSRF: una API debrid maliciosa/caída devolviendo link-local o
        reserved -> None sin tocar la red (guard _public_url_cached)."""
        s, _ = fake_session({'json': {}})
        for u in ('http://169.254.169.254/latest/meta-data',
                  'http://[::1]/x', 'gopher://x/'):
            with patch.object(db, '_db_resolve', return_value=u), \
                    patch.object(db, '_session', return_value=s) as m:
                self.assertIsNone(db._db_stream('real-debrid', 'h', 0, {}), u)
                m.assert_not_called()


class TestResolveFi(unittest.TestCase):
    """?fi= del query: malformado -> 0 (no crash ni fallo del resolve)."""

    @patch('conflux.infra.debrid._db_key', return_value='k')
    def test_fi_malformado_cae_a_cero(self, _):
        m = unittest.mock.Mock(return_value='http://dl/x')
        # _DB_RESOLVE guarda referencia directa: hay que parchear el dict
        with unittest.mock.patch.dict(db._DB_RESOLVE, {'real-debrid': m}):
            self.assertEqual(db._db_resolve('real-debrid', 'h', 'abc'), 'http://dl/x')
        self.assertEqual(m.call_args[0][2], 0)


class TestTimeoutsProtocolo(_DbBase):
    """Timeouts por llamada y miss[:40] son contrato (6+1/8+1/10+1/15+1/
    30+1/40+1 y Eq@135)."""
    H = 'e' * 40

    def _calls(self, fn, scripts, *args):
        s, calls = fake_session(*scripts)
        with patch.object(db, '_session', return_value=s):
            fn(*args)
        return calls

    def test_dbc_timeout_y_cap(self):
        cases = [
            (db._dbc_rd, [{'json': {}}], 6),
            (db._dbc_tb, [{'json': {'data': {}}}], 6),
            (db._dbc_ad, [{'json': {'data': {'magnets': []}}}], 6),
            (db._dbc_pm, [{'json': {'response': []}}], 6),
            (db._dbc_ed, [{'json': []}], 6),
            (db._dbc_dl, [{'json': {'value': False}}], 6),
            (db._dbc_oc, [{'json': {}}], 8),
            (db._dbc_pio, [{'json': {'transfers': []}}], 8),
        ]
        for fn, scripts, t in cases:
            calls = self._calls(fn, scripts, 'k', [self.H])
            self.assertEqual(calls[0][2]['timeout'], t, fn.__name__)

    def test_dbc_miss_cap_40(self):
        """miss[:40] — 41 hashes -> el POST lleva solo 40 (40+1@60/93)."""
        hashes = [f'{i:040x}' for i in range(41)]
        calls = self._calls(db._dbc_ad, [{'json': {'data': {'magnets': []}}}],
                            'k', hashes)
        self.assertEqual(len(calls[0][2]['data']), 40)
        calls = self._calls(db._dbc_rd, [{'json': {}}], 'k', hashes)
        path = calls[0][1].split('instantAvailability/')[1]
        self.assertEqual(len(path.split('/')), 40)

    def test_dbc_pm_zip_cap_40(self):
        """El zip del resultado también corta a 40 (40+1@93: 41 flags
        marcarían un 41º hash)."""
        hashes = [f'{i:040x}' for i in range(41)]
        s, _ = fake_session({'json': {'response': [True] * 41}})
        out = {}
        with patch.object(db, '_session', return_value=s):
            db._dbc_pm('k', hashes, out)
        self.assertEqual(len(out), 40)
        self.assertNotIn(hashes[40], out)

    def test_oc_status_cached_exacto(self):
        """status == 'cached' (Eq@135: != marcaría todo lo contrario)."""
        s, _ = fake_session({'json': {
            f'magnet:?xt=urn:btih:{self.H}': {'status': 'cached'},
            'x': {'status': 'downloading'},
            'y': True}})
        out = {}
        with patch.object(db, '_session', return_value=s):
            db._dbc_oc('k', [self.H, 'f' * 40], out)
        self.assertTrue(out[self.H])
        self.assertFalse(out['f' * 40])

    def test_resolver_timeouts(self):
        """rd=8×5, tb/ad/ed/dl=10, pm/oc/pio=15+10, usenet=15/10, stream=30."""
        cases = [
            (db._db_rd, (self.H, 0), [
                {'json': {'id': 't1'}},
                {'json': {'files': [{'id': 1, 'name': 'v.mkv', 'bytes': 9}]}},
                {'json': {}}, {'json': {'links': ['L']}},
                {'json': {'download': 'd'}}], [8, 8, 8, 8, 8]),
            (db._db_tb, (self.H, 0), [
                {'json': {'data': {'torrent_id': 9}}},
                {'json': {'data': {'link': 'd'}}}], [10, 10]),
            (db._db_ad, (self.H, 0), [
                {'json': {'data': {'magnets': [{'id': 'm1'}]}}},
                {'json': {'data': {'magnets': {'links': [
                    {'filename': 'v.mkv', 'link': 'L'}]}}}},
                {'json': {'data': {'link': 'd'}}}], [10, 10, 10]),
            (db._db_pm, (self.H, 0), [
                {'json': {'content': [{'path': 'a/v.mkv', 'link': 'L'}]}}], [15]),
            (db._db_ed, (self.H, 0), [
                {'json': {'files': [{'filename': 'v.mkv', 'url': 'EU'}]}}], [10]),
            (db._db_dl, (self.H, 0), [
                {'json': {'value': {'id': 'i1'}}},
                {'json': {'value': [{'name': 'v.mkv',
                                     'downloadUrl': 'DU'}]}}], [10, 10]),
            (db._db_oc, (self.H, 0), [
                {'json': {'requestId': 'r1', 'status': 'downloaded',
                          'url': 'https://x/'}},
                {'json': [{'url': 'd/v.mkv'}]}], [15, 10]),
            (db._db_pio, (self.H, 0), [
                {'json': {'id': 5}},
                {'json': {'files': [{'name': 'v.mkv', 'id': 7,
                                     'download_url': 'DU'}]}}], [15, 10]),
        ]
        for fn, (h, fi), scripts, touts in cases:
            calls = self._calls(fn, scripts, 'k', h, fi)
            self.assertEqual([c[2].get('timeout') for c in calls], touts,
                             fn.__name__)

    def test_usenet_timeouts(self):
        """_db_usenet: create 15, mylist/request 10."""
        old = dict(st.CFG)
        st.CFG['debrid_providers'] = {'torbox': 'k'}
        s, calls = fake_session(
            {'json': {'data': {'usenetdownload_id': '7'}}},
            {'json': {'data': [{'usenetdownload_id': '7',
                                'files': [{'id': 3, 'name': 'v.mkv',
                                           'size': 9}]}]}},
            {'json': {'data': {'link': 'https://u/nzb'}}})
        try:
            with patch.object(db, '_session', return_value=s):
                db._db_usenet('https://nzb/f')
        finally:
            st.CFG.clear(); st.CFG.update(old)
        self.assertEqual([c[2].get('timeout') for c in calls], [15, 10, 10])


class TestDbStreamTimeout(_DbBase):
    H = 'e' * 40

    def test_stream_timeout_30(self):
        """_db_stream reenvía con timeout=30 (30+1@443)."""
        from unittest.mock import MagicMock
        s = MagicMock()
        with patch.object(db, '_session', return_value=s), \
                patch.object(db, '_db_resolve', return_value='https://x/f.mkv'):
            db._db_stream('rd', self.H, 0, {'Range': 'bytes=0-1'})
        self.assertEqual(s.get.call_args[1]['timeout'], 30)


class TestMultiDebrid(unittest.TestCase):
    """_db_cfgs/_db_key/_db_avail_map — N providers simultáneos."""
    H1, H2 = 'e' * 40, 'f' * 40

    def setUp(self):
        self._old = dict(st.CFG)
        db._DB_OK.clear()

    def tearDown(self):
        st.CFG.clear(); st.CFG.update(self._old)
        db._DB_OK.clear()

    def test_cfgs_legacy_primero_despues_dict(self):
        st.CFG.update(debrid_provider='real-debrid', debrid_key='k1',
                      debrid_providers={'torbox': 'k2', 'alldebrid': 'k3'})
        self.assertEqual(db._db_cfgs(),
                         [('real-debrid', 'k1'), ('torbox', 'k2'), ('alldebrid', 'k3')])

    def test_cfgs_dedup_y_desconocido(self):
        st.CFG.update(debrid_provider='torbox', debrid_key='k1',
                      debrid_providers={'torbox': 'otra', 'nope': 'k', '': 'x'})
        cfgs = db._db_cfgs()
        self.assertEqual(len(cfgs), 1)                    # torbox dedup; nope/'' fuera
        self.assertEqual(cfgs[0], ('torbox', 'k1'))

    def test_db_key_por_provider(self):
        st.CFG.update(debrid_provider='real-debrid', debrid_key='k1',
                      debrid_providers={'torbox': 'k2'})
        self.assertEqual(db._db_key('torbox'), 'k2')
        self.assertEqual(db._db_key('real-debrid'), 'k1')
        self.assertIsNone(db._db_key('premiumize'))

    def test_resolve_usa_key_del_provider(self):
        """Bug latente: /r/torbox/ resolvía con la key del provider principal."""
        st.CFG.update(debrid_provider='real-debrid', debrid_key='kRD',
                      debrid_providers={'torbox': 'kTB'})
        m = unittest.mock.Mock(return_value='http://dl/x')
        with unittest.mock.patch.dict(db._DB_RESOLVE, {'torbox': m}):
            db._db_resolve('torbox', self.H1, 0)
        self.assertEqual(m.call_args[0][0], 'kTB')      # key de torbox, no de RD

    def test_avail_map_prioridad_config(self):
        """Los dos lo tienen -> gana el primero configurado (orden cfg)."""
        st.CFG.update(debrid_provider='real-debrid', debrid_key='k',
                      debrid_providers={'torbox': 'k2'})

        def hit_all(k, miss, out):
            for h in miss:
                out[h] = True
        with unittest.mock.patch.dict(db._DB_CHECK,
                                      {'real-debrid': hit_all, 'torbox': hit_all}):
            got = db._db_avail_map([self.H1])
        self.assertEqual(got[self.H1], 'real-debrid')   # primero en cfg gana
        self.assertEqual(db._DB_OK[self.H1], 'real-debrid')

    def test_avail_map_segundo_si_primero_no(self):
        """Solo lo tiene torbox -> provider torbox en el mapa."""
        st.CFG.update(debrid_provider='real-debrid', debrid_key='k',
                      debrid_providers={'torbox': 'k2'})

        def rd_miss(k, miss, out):
            for h in miss:
                out[h] = False

        def tb_hit(k, miss, out):
            for h in miss:
                out[h] = True
        with unittest.mock.patch.dict(db._DB_CHECK,
                                      {'real-debrid': rd_miss, 'torbox': tb_hit}):
            got = db._db_avail_map([self.H1, self.H2])
        self.assertEqual(got[self.H1], 'torbox')
        self.assertEqual(db._db_avail([self.H1]), {self.H1})

    def test_marca_con_tag_multi(self):
        """Multi-provider: el nombre lleva el tag [XX] del que lo sirve."""
        st.CFG.update(debrid_provider='real-debrid', debrid_key='k',
                      debrid_providers={'torbox': 'k2'})
        s = {'infoHash': self.H1, 'name': 'n', 'fileIdx': 0}
        db._db_mark_one('torbox', s)
        self.assertTrue(s['name'].startswith('⚡ [TB]'))

    def test_marca_sin_tag_mono(self):
        """Un solo provider: formato clásico sin tag (compat)."""
        st.CFG.update(debrid_provider='torbox', debrid_key='k',
                      debrid_providers={})
        s = {'infoHash': self.H1, 'name': 'n', 'fileIdx': 0}
        db._db_mark_one('torbox', s)
        self.assertTrue(s['name'].startswith('⚡ '))
        self.assertNotIn('[', s['name'])


class TestBatchCaps(_DbBase):
    """miss[:40] (o [:20] en debrid-link): los checkers trocean el lote —
    el hash 41 nunca viaja ni se evalúa (mutantes const 40+1/20+1)."""
    H41 = ['%040x' % i for i in range(41)]

    def test_rd_batch_40(self):
        s, calls = fake_session({'json': {}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_rd('k', self.H41)
        self.assertNotIn(self.H41[40], calls[0][1])
        self.assertEqual(len(db._DB_OK), 40)

    def test_tb_batch_40(self):
        s, calls = fake_session({'json': {'data': {}}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_tb('k', self.H41)
        self.assertNotIn(self.H41[40], calls[0][1])
        self.assertEqual(len(db._DB_OK), 40)

    def test_ad_batch_40(self):
        s, calls = fake_session({'json': {'data': {'magnets': []}}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_ad('k', self.H41)
        self.assertEqual(len(calls[0][2]['data']), 40)
        self.assertEqual(len(db._DB_OK), 40)

    def test_pm_batch_40(self):
        s, calls = fake_session({'json': {'response': []}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_pm('k', self.H41)
        self.assertEqual(len(calls[0][2]['data']), 40)

    def test_ed_batch_40(self):
        s, calls = fake_session({'json': []})
        with patch.object(db, '_session', return_value=s):
            db._dbc_ed('k', self.H41)
        self.assertEqual(len(calls[0][2]['json']['urls']), 40)
        self.assertEqual(len(db._DB_OK), 40)

    def test_oc_batch_40(self):
        s, calls = fake_session({'json': {}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_oc('k', self.H41)
        self.assertEqual(len(calls[0][2]['data']), 40)
        self.assertEqual(len(db._DB_OK), 40)

    def test_dl_batch_20(self):
        s, calls = fake_session({'json': {'value': True}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_dl('k', self.H41[:21])
        self.assertEqual(len(calls), 20)             # una petición por magnet
        self.assertEqual(len(db._DB_OK), 20)

    def test_pio_batch_40(self):
        s, _ = fake_session({'json': {'transfers': []}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_pio('k', self.H41)
        self.assertEqual(len(db._DB_OK), 40)


class TestCheckersEdges(_DbBase):
    """Ramas blandas de los checkers (mutantes boolop/cmp/const)."""
    H = '7' * 40

    def test_pm_flags_menos_que_miss(self):
        """response más corto que miss: zip lenient rellena lo que hay
        (mutante strict=False->True lanzaba ValueError -> todo miss)."""
        s, _ = fake_session({'json': {'response': [True]}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_pm('k', [self.H, 'b' * 40])
        self.assertTrue(db._DB_OK[self.H])

    def test_ed_data_vacia_results(self):
        """data:[] + results:[hit] -> results se usa (mutante Or->And)."""
        s, _ = fake_session({'json': {'data': [], 'results': [
            {'url': f'magnet:?xt=urn:btih:{self.H}', 'cached': True}]}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_ed('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_ed_cached_ausente_es_true(self):
        """Item sin clave 'cached' -> default True (mutante True->False)."""
        s, _ = fake_session({'json': [
            {'url': f'magnet:?xt=urn:btih:{self.H}'}]})
        with patch.object(db, '_session', return_value=s):
            db._dbc_ed('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_ed_hash_ausente_no_hit(self):
        """Hash no devuelto por el checker -> False, no hit (mutante In->NotIn)."""
        s, _ = fake_session({'json': [
            {'url': 'magnet:?xt=urn:btih:' + 'z' * 40, 'cached': True}]})
        with patch.object(db, '_session', return_value=s):
            db._dbc_ed('k', [self.H])
        self.assertFalse(db._DB_OK[self.H])

    def test_oc_valor_true_directo(self):
        """Offcloud: valor booleano (no dict) True -> hit (Is/True@135)."""
        s, _ = fake_session({'json': {
            f'magnet:?xt=urn:btih:{self.H}': True}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_oc('k', [self.H])
        self.assertTrue(db._DB_OK[self.H])

    def test_oc_status_no_cached_miss(self):
        """status 'downloading' (dict) no es 'cached' -> miss."""
        s, _ = fake_session({'json': {
            f'magnet:?xt=urn:btih:{self.H}': {'status': 'downloading'}}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_oc('k', [self.H])
        self.assertFalse(db._DB_OK[self.H])

    def test_pio_per_page_100(self):
        s, calls = fake_session({'json': {'transfers': []}})
        with patch.object(db, '_session', return_value=s):
            db._dbc_pio('k', [self.H])
        self.assertEqual(calls[0][2]['params']['per_page'], 100)


class TestDbCfgEdge(_DbBase):
    def test_provider_vacio_con_key_no_cfg(self):
        """provider='' con key -> NO se añade: exige p AND k (And@32)."""
        st.CFG['debrid_provider'] = ''
        self.assertEqual(db._db_cfgs(), [])

    def test_avail_sin_cfgs_no_explota(self):
        """Sin providers: {} sin tocar la red (mutante `cfgs and miss`->or
        lanzaba ThreadPoolExecutor(max_workers=0) -> ValueError)."""
        st.CFG.clear()
        self.assertEqual(db._db_avail_map(['a' * 40]), {})

    def test_db_ok_cap_5000(self):
        """_DB_OK >5000 -> se vacía y re-chequea (Gt/5000+1@169)."""
        db._DB_OK.update({'%040x' % i: 'rd' for i in range(5001)})
        st.CFG['debrid_provider'] = 'real-debrid'
        s, _ = fake_session({'json': {}})
        with patch.object(db, '_session', return_value=s):
            db._db_avail_map(['a' * 40])
        self.assertLess(len(db._DB_OK), 5000)        # purgado y repoblado

    def test_db_ok_exacto_5000_no_purga(self):
        """Exactamente 5000 -> NO purga: la cota es >5000 no >=5000 (Gt@169)."""
        db._DB_OK.update({'%040x' % i: 'rd' for i in range(5000)})
        s, _ = fake_session({'json': {}})
        with patch.object(db, '_session', return_value=s):
            db._db_avail_map(['b' * 40])
        self.assertEqual(len(db._DB_OK), 5001)       # 5000 + el nuevo


class TestMarkEdge(_DbBase):
    def test_nombre_original_conservado(self):
        """'⚡ ' + tag + nombre — el nombre NO se pierde (Or@224)."""
        st.CFG.update(debrid_provider='real-debrid', debrid_key='k',
                      debrid_providers={'torbox': 'k2'})
        s = {'infoHash': 'h', 'name': 'YTS', 'fileIdx': 0}
        db._db_mark_one('torbox', s)
        self.assertTrue(s['name'].endswith('YTS'))
