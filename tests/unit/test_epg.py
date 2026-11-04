# pylint: disable=missing-docstring,protected-access,import-outside-toplevel
"""Unit tests conflux.infra.epg — XMLTV -> parrilla por canal m3u."""
import gzip
import io
import time
import unittest

import conflux.infra.epg as E


def _xmltv(programas, canales=('la1.es', 'la2.es')):
    ch = ''.join(f'<channel id="{c}"><display-name>{c.split(".")[0]}</display-name></channel>'
                 for c in canales)
    pr = ''.join(
        f'<programme channel="{ch_}" start="{s}" stop="{t}">'
        f'<title>{tit}</title><desc>{d}</desc><icon src="{i}"/></programme>'
        for ch_, s, t, tit, d, i in programas)
    return ('<tv>' + ch + pr + '</tv>').encode()


def _ts(dt):                                     # epoch -> 'yyyymmddhhmmss +0000'
    return time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(dt))


class TestNorm(unittest.TestCase):
    def test_sufijos_y_tildes(self):
        self.assertEqual(E._norm('La1.es@SD'), 'la1es')    # @SD fuera; '.es' se queda
        self.assertEqual(E._norm('La Sexta HD'), 'lasextahd')
        self.assertEqual(E._norm('ANTENA 3.es'), 'antena3es')
        self.assertEqual(E._norm(''), '')

    def test_mapeo_equivalente(self):
        self.assertEqual(E._norm('LA1.ES'), E._norm('la1.es'))
        self.assertEqual(E._norm('la-1.es'), E._norm('la1.es'))


class TestXmltvTs(unittest.TestCase):
    def test_con_tz(self):
        ts = E._xmltv_ts('20251103220000 +0100')
        self.assertIsInstance(ts, float)

    def test_sin_tz(self):
        self.assertIsInstance(E._xmltv_ts('20251103220000'), float)

    def test_malformado_none(self):
        self.assertIsNone(E._xmltv_ts(''))
        self.assertIsNone(E._xmltv_ts('noesfecha'))
        self.assertIsNone(E._xmltv_ts(None))

    def test_tz_afecta(self):
        a = E._xmltv_ts('20251103220000 +0200')
        b = E._xmltv_ts('20251103220000 +0000')
        self.assertEqual(b - a, 7200)


class TestIso(unittest.TestCase):
    def test_formato(self):
        self.assertEqual(E._iso(0), '1970-01-01T00:00:00.000Z')


class TestBuild(unittest.TestCase):
    """_build: descarga XMLTV (gzip o plano) -> map + days, solo canales m3u."""

    def setUp(self):
        self._cfg = dict(E.CFG)
        self._open = E._OPENER.open
        self._pub = E._public_url
        E._public_url = lambda u: u.startswith('https://')   # DNS real fuera

    def tearDown(self):
        E.CFG.clear()
        E.CFG.update(self._cfg)
        E._OPENER.open = self._open
        E._public_url = self._pub

    def _fake_open(self, data, gz=True):
        raw = gzip.compress(data) if gz else data
        E._OPENER.open = lambda req, timeout=0: io.BytesIO(raw)

    def test_sin_url_none(self):
        E.CFG['epg_url'] = ''
        self.assertIsNone(E._build([{'id': 'c1'}]))

    def test_url_privada_none(self):
        E._public_url = self._pub                    # guard real con IP literal
        E.CFG['epg_url'] = 'http://169.254.169.254/latest'   # metadata cloud
        self.assertIsNone(E._build([{'id': 'c1'}]))

    def test_build_gz_mapea_y_recorta(self):
        import unittest.mock as um
        E.CFG['epg_url'] = 'https://epg.test/x.xml.gz'
        now = time.time()
        xml = _xmltv([
            ('la1.es', _ts(now - 3600), _ts(now - 1800), 'Hoy', 'd1', 'http://i/1.png'),
            ('la1.es', _ts(now + 30 * 3600), _ts(now + 31 * 3600), 'Mañana', 'd2', ''),
            ('la1.es', _ts(now - 60 * 3600), _ts(now - 59 * 3600), 'Fuera', '', ''),
            ('desconocido', _ts(now), _ts(now + 60), 'NoMapeado', '', ''),
        ], canales=('la1.es',))
        self._fake_open(xml)
        metas = [{'id': 'la1', 'tvg': 'la1.es', 'name': 'La 1'},
                 {'id': 'cnn', 'tvg': 'cnn.es', 'name': 'CNN'}]      # sin epg
        with um.patch.object(E, '_log'):
            out = E._build(metas)
        self.assertIsNotNone(out)
        self.assertEqual(out['map'], {'la1': 'la1.es'})
        dia = time.strftime('%Y-%m-%d', time.gmtime(now - 3600))
        progs = out['days'][dia]['la1']
        self.assertEqual([p['title'] for p in progs], ['Hoy'])
        self.assertEqual(progs[0]['icon'], 'http://i/1.png')
        total = sum(len(p) for d in out['days'].values() for p in d.values())
        self.assertEqual(total, 2)                      # Fuera/NoMapeado recortados

    def test_xml_plano_sin_gz(self):
        import unittest.mock as um
        E.CFG['epg_url'] = 'https://epg.test/x.xml'
        now = time.time()
        xml = _xmltv([('la1.es', _ts(now), _ts(now + 60), 'P', '', '')],
                     canales=('la1.es',))
        self._fake_open(xml, gz=False)
        with um.patch.object(E, '_log'):
            out = E._build([{'id': 'la1', 'tvg': 'la1.es'}])
        self.assertEqual(out['map'], {'la1': 'la1.es'})

    def test_sin_canales_mapeados_none(self):
        E.CFG['epg_url'] = 'https://epg.test/x.xml'
        self._fake_open(_xmltv([('la1.es', _ts(time.time()),
                                 _ts(time.time() + 60), 'P', '', '')]),
                        gz=False)
        import unittest.mock as um
        with um.patch.object(E, '_log'):
            self.assertIsNone(E._build([{'id': 'x', 'tvg': 'noexiste'}]))


class TestStateIO(unittest.TestCase):
    """_videos/epg_metas/epg_meta con _STATE sembrado."""

    def setUp(self):
        self._st = dict(E._STATE)
        self._kick = E.epg_kick
        E.epg_kick = lambda: None
        now = time.time()
        dia = time.strftime('%Y-%m-%d', time.gmtime(now))
        E._STATE['days'] = {dia: {'la1': [
            {'title': 'A', 'desc': 'd', 'icon': 'i', 'start': now, 'stop': now + 60},
            {'title': 'B', 'desc': '', 'icon': '', 'start': now + 60, 'stop': now + 120}]}}

    def tearDown(self):
        E._STATE.clear()
        E._STATE.update(self._st)
        E.epg_kick = self._kick

    def test_videos_formato(self):
        dia = next(iter(E._STATE['days']))
        vs = E._videos('la1', dia)
        self.assertEqual(len(vs), 2)
        self.assertEqual(vs[0]['id'], 'la1:0')
        self.assertEqual(vs[0]['name'], 'A')
        self.assertEqual(vs[1]['episode'], 2)
        self.assertTrue(vs[0]['released'].endswith('.000Z'))
        self.assertEqual(vs[0]['startTime'], vs[0]['released'])

    def test_videos_canal_desconocido(self):
        self.assertEqual(E._videos('cnn', '2099-01-01'), [])

    def test_epg_metas_adjunta_videos(self):
        dia = next(iter(E._STATE['days']))
        out = E.epg_metas(dia, [{'id': 'la1'}, {'id': 'cnn'}])
        self.assertEqual(len(out['metasDetailed']), 2)
        self.assertEqual(len(out['metasDetailed'][0]['videos']), 2)
        self.assertEqual(out['metasDetailed'][1]['videos'], [])

    def test_epg_meta_hoy(self):
        m = E.epg_meta({'id': 'la1'})
        self.assertEqual(len(m['videos']), 2)
        self.assertEqual(E.epg_meta(None), {})


class TestKick(unittest.TestCase):
    def setUp(self):
        self._st = dict(E._STATE)
        self._loading = E._LOADING[0]
        self._sub = E._POOL_BG.submit
        self.enviados = []
        E._POOL_BG.submit = lambda f, *a: self.enviados.append(f)

    def tearDown(self):
        E._STATE.clear()
        E._STATE.update(self._st)
        E._LOADING[0] = self._loading
        E._POOL_BG.submit = self._sub

    def test_fresco_no_kick(self):
        E._STATE['days'] = {'2099-01-01': {}}
        E._STATE['fetched'] = time.time()
        E.epg_kick()
        self.assertEqual(self.enviados, [])

    def test_vacio_kick_una_vez(self):
        E._STATE['days'] = {}
        E.epg_kick()
        E._LOADING[0] = True                        # sigue en vuelo
        E.epg_kick()                                # no duplica
        self.assertEqual(self.enviados, [E._reload])


class TestRedirectSSRF(unittest.TestCase):
    def test_redirect_privado_bloqueado(self):
        h = E._ChkRedirect()
        r = h.redirect_request(None, None, 302, '', {},
                               'http://169.254.169.254/latest/meta-data')
        self.assertIsNone(r)

    def test_redirect_publico_pasa(self):
        import unittest.mock as um
        import urllib.request
        h = E._ChkRedirect()
        req = urllib.request.Request('https://epg.test/x')
        with um.patch.object(E, '_public_url', return_value=True):
            r = h.redirect_request(req, None, 302, '', {}, 'https://epg.test/ok')
        self.assertIsNotNone(r)
