# pylint: disable=missing-docstring,protected-access,import-outside-toplevel
"""Unit tests conflux.infra.epg — XMLTV -> parrilla por canal m3u."""
import gzip
import io
import os
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

    def test_gz_bomb_acotada(self):
        """zip-bomb: el gz cabe en 40MB pero infla > cap -> se lee acotado
        (truncado -> XML incompleto -> None por _reload, no OOM).
        _GZ_CAP parcheado a 1KB para no mover 160MB en el test."""
        import unittest.mock as um
        E.CFG['epg_url'] = 'https://epg.test/x.xml.gz'
        E._OPENER.open = lambda req, timeout=0: io.BytesIO(
            gzip.compress(b'<tv>' + b'x' * 2000))
        with um.patch.object(E, '_GZ_CAP', 1024), \
                um.patch.object(E, '_log'):
            self.assertIsNone(E._build([{'id': 'c1'}]))     # truncado: sin canales -> None

    def test_gz_truncado_no_lanza(self):
        """gzip partido a la mitad: EOFError -> cae a raw -> None controlado."""
        import unittest.mock as um
        E.CFG['epg_url'] = 'https://epg.test/x.xml.gz'
        raw = gzip.compress(_xmltv([('la1.es', _ts(time.time()),
                                    _ts(time.time() + 60), 'P', '', '')],
                                   canales=('la1.es',)))
        E._OPENER.open = lambda req, timeout=0: io.BytesIO(raw[:len(raw) // 2])
        with um.patch.object(E, '_log'):
            self.assertIsNone(E._build([{'id': 'la1', 'tvg': 'la1.es'}]))


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


class TestTzOffset(unittest.TestCase):
    def test_minutos_de_tz_cuentan(self):
        """'+0130' = 3600+1800 = 5400s (Mult/Add@59, Sub@61 cambian la resta)."""
        a = E._xmltv_ts('20251103220000 +0130')
        b = E._xmltv_ts('20251103220000 +0000')
        self.assertEqual(b - a, 5400)
        c = E._xmltv_ts('20251103220000 -0330')
        self.assertEqual(b - c, -12600)   # -3h30m: signo tb en los minutos


class TestBuildVentana(unittest.TestCase):
    """lo=now-26h, hi=now+2d — bordes exactos (26/3600/2/86400+1@93, LtE@101)."""

    def setUp(self):
        self._cfg = dict(E.CFG)
        self._open = E._OPENER.open
        self._pub = E._public_url
        E._public_url = lambda u: True
        E.CFG['epg_url'] = 'https://epg.test/x.xml'

    def tearDown(self):
        E.CFG.clear(); E.CFG.update(self._cfg)
        E._OPENER.open = self._open
        E._public_url = self._pub

    def _build(self, progs):
        from freezegun import freeze_time
        xml = _xmltv(progs, canales=('la1.es',))
        E._OPENER.open = lambda req, timeout=0: io.BytesIO(xml)
        import unittest.mock as um
        with freeze_time('2025-06-01 12:00:00'), um.patch.object(E, '_log'):
            return E._build([{'id': 'la1', 'tvg': 'la1.es'}])

    def _tslocal(self, dt):
        import time as _t
        return _t.strftime('%Y%m%d%H%M%S +0000', _t.gmtime(dt))

    def test_borde_ventana(self):
        from freezegun import freeze_time
        with freeze_time('2025-06-01 12:00:00'):
            import time as _t
            now = _t.time()
        progs = [
            ('la1.es', self._tslocal(now - 26 * 3600), self._tslocal(now - 25 * 3600), 'Borde26', 'dd', ''),
            ('la1.es', self._tslocal(now - 27 * 3600), self._tslocal(now - 26 * 3600), 'Fuera27', '', ''),
            ('la1.es', self._tslocal(now - 26 * 3600 - 20), self._tslocal(now - 25 * 3600), 'Fuera26y20s', '', ''),
            ('la1.es', self._tslocal(now + 47 * 3600), self._tslocal(now + 48 * 3600), 'Dentro47', '', ''),
            ('la1.es', self._tslocal(now + 48 * 3600), self._tslocal(now + 49 * 3600), 'Borde48', '', ''),
            ('la1.es', self._tslocal(now + 48 * 3600 + 1), self._tslocal(now + 50 * 3600), 'Fuera48y1s', '', ''),
            ('la1.es', self._tslocal(now + 49 * 3600), self._tslocal(now + 50 * 3600), 'Fuera49', '', ''),
        ]
        out = self._build(progs)
        titles = [p['title'] for d in out['days'].values()
                  for ps in d.values() for p in ps]
        self.assertIn('Borde26', titles)
        self.assertIn('Dentro47', titles)
        self.assertIn('Borde48', titles)
        self.assertNotIn('Fuera27', titles)
        self.assertNotIn('Fuera26y20s', titles)
        self.assertNotIn('Fuera48y1s', titles)
        self.assertNotIn('Fuera49', titles)

    def test_desc_conservada(self):
        """(p.findtext('desc') or '') (Or@104: and -> '')."""
        from freezegun import freeze_time
        with freeze_time('2025-06-01 12:00:00'):
            import time as _t
            now = _t.time()
        out = self._build([('la1.es', self._tslocal(now), self._tslocal(now + 60),
                            'T', 'descripcion-real', '')])
        prog = next(p for d in out['days'].values()
                    for ps in d.values() for p in ps)
        self.assertEqual(prog['desc'], 'descripcion-real')

    def test_read_cap_y_timeout(self):
        """read(40MB) cap + open(timeout=60) (60/40000000+1@122)."""
        import unittest.mock as um
        resp = um.MagicMock()
        resp.read.return_value = b''
        op = um.MagicMock(return_value=resp)
        with um.patch.object(E._OPENER, 'open', op), \
                um.patch.object(E, '_log'):
            E._build([{'id': 'la1', 'tvg': 'la1.es'}])
        self.assertEqual(op.call_args[1].get('timeout') or op.call_args[0][1], 60)
        self.assertEqual(resp.read.call_args[0][0], 40_000_000)


class TestReloadDiscoYLoading(unittest.TestCase):
    """_reload: cache disco solo si fetched==0 y fresco (Eq/And@142, Sub/Lt@149);
    _LOADING se libera en finally (False@164)."""

    def setUp(self):
        self._st = dict(E._STATE); self._ld = E._LOADING[0]
        self._kick = E.epg_kick
        E.epg_kick = lambda: None                 # sin _reload en background
        E._STATE.clear(); E._STATE.update({'days': {}, 'map': {}, 'fetched': 0})
        E._LOADING[0] = False

    def tearDown(self):
        E._STATE.clear(); E._STATE.update(self._st)
        E._LOADING[0] = self._ld
        E.epg_kick = self._kick

    def _disco(self, disk):
        """_EPG_FILE -> fichero temporal con `disk`; devuelve ctx managers."""
        import json
        import tempfile
        fd, path = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(disk, fh)
        self.addCleanup(os.unlink, path)
        return path

    def test_disco_solo_si_fetched_cero(self):
        """fetched!=0 + fichero existe -> el disco NO pisa el estado en caliente
        (Eq/And@142)."""
        import unittest.mock as um
        path = self._disco({'days': {'d': {}}, 'map': {'x': 'y'},
                           'fetched': time.time()})
        with um.patch.object(E, '_EPG_FILE', path), \
                um.patch('conflux.domain.lists._m3u_metas',
                         return_value=[]) as mm, \
                um.patch.object(E, '_build'):
            E._STATE['fetched'] = 12345.0
            E._reload()
        mm.assert_called_once()                    # fue a la ruta de fetch
        self.assertEqual(E._STATE['map'], {})      # el disco no pisó

    def test_disco_fresco_evita_fetch(self):
        """fetched==0 + disco fresco -> carga y return (Sub/Lt@149)."""
        import unittest.mock as um
        path = self._disco({'days': {'d': {}}, 'map': {'x': 'y'},
                           'fetched': time.time()})
        with um.patch.object(E, '_EPG_FILE', path), \
                um.patch('conflux.domain.lists._m3u_metas') as mm:
            E._reload()
        mm.assert_not_called()
        self.assertEqual(E._STATE['map'], {'x': 'y'})

    def test_disco_viejo_refresca(self):
        """disco con edad > TTL -> ignora y fetch (Lt@149: <= lo creería fresco)."""
        import unittest.mock as um
        path = self._disco({'days': {'d': {}}, 'map': {},
                           'fetched': time.time() - E._EPG_TTL - 10})
        with um.patch.object(E, '_EPG_FILE', path), \
                um.patch('conflux.domain.lists._m3u_metas',
                         return_value=[]) as mm:
            E._reload()
        mm.assert_called_once()

    def test_loading_se_libera(self):
        """finally: _LOADING[0]=False siempre (False@164)."""
        import unittest.mock as um
        with um.patch.object(E, '_EPG_FILE', '/no/existe.json'), \
                um.patch('conflux.domain.lists._m3u_metas', return_value=[]):
            E._LOADING[0] = True
            E._reload()
        self.assertFalse(E._LOADING[0])

    def test_metas_presentes_fetcha(self):
        """not metas -> return (not-elim@154: con metas retornaría)."""
        import unittest.mock as um
        with um.patch.object(E, '_EPG_FILE', '/no/existe.json'), \
                um.patch('conflux.domain.lists._m3u_metas',
                         return_value=[{'id': 'c', 'tvg': 'x'}]), \
                um.patch.object(E, '_build',
                                return_value={'days': {}, 'map': {},
                                              'stats': {}}) as bd, \
                um.patch.object(E, '_atomic_json'):
            E._reload()
        bd.assert_called_once()


class TestKickStale(unittest.TestCase):
    """stale = (not days) or (age > TTL) — Gt@169, Or@169, True@175, TTL@26."""

    def setUp(self):
        self._st = dict(E._STATE); self._ld = E._LOADING[0]
        self._sub = E._POOL_BG.submit
        self.enviados = []
        E._POOL_BG.submit = lambda f, *a: self.enviados.append(f)
        E._LOADING[0] = False

    def tearDown(self):
        E._STATE.clear(); E._STATE.update(self._st)
        E._LOADING[0] = self._ld
        E._POOL_BG.submit = self._sub

    def test_ttl_valor(self):
        self.assertEqual(E._EPG_TTL, 43200)         # 12*3600 (@26)

    def test_vacio_aunque_fresco_stale(self):
        """days vacío -> stale aunque fetched sea reciente (Or@169: and no)."""
        E._STATE['days'] = {}
        E._STATE['fetched'] = time.time()
        E.epg_kick()
        self.assertEqual(self.enviados, [E._reload])

    def test_fresco_exacto_ttl_no_kick(self):
        """age == TTL no stale (Gt@169: >= dispararía)."""
        from freezegun import freeze_time
        with freeze_time('2025-06-01 12:00:00'):
            E._STATE['days'] = {'2099-01-01': {}}
            E._STATE['fetched'] = time.time() - E._EPG_TTL
            E.epg_kick()
        self.assertEqual(self.enviados, [])

    def test_doble_kick_no_duplica(self):
        """_LOADING[0]=True antes de submit (True@175: False -> doble)."""
        E._STATE['days'] = {}
        E.epg_kick(); E.epg_kick()
        self.assertEqual(self.enviados, [E._reload])


class TestEpgMetaManana(unittest.TestCase):
    """epg_meta: hoy + hasta 6 de mañana (Add/86400+1@210, 6+1@212, Add@185)."""

    def setUp(self):
        self._kick = E.epg_kick
        E.epg_kick = lambda: None

    def tearDown(self):
        E.epg_kick = self._kick

    def test_manana_cap_y_episodios(self):
        import unittest.mock as um
        calls = []
        def fake_vids(cid, day):
            calls.append(day)
            return [{'id': f'{cid}:h{i}', 'episode': i} for i in range(8)]
        with um.patch.object(E, '_videos', side_effect=fake_vids):
            m = E.epg_meta({'id': 'la1'})
        import time as _t
        today = _t.strftime('%Y-%m-%d', _t.gmtime())
        self.assertEqual(calls[0], today)
        self.assertNotEqual(calls[1], today)       # mañana = today+86400
        # hoy(8) + mañana[:6] = 14 vídeos total
        self.assertEqual(len(m['videos']), 14)

    def test_episodio_secuencia_desde_1(self):
        """'episode': i+1 — el primero es 1, no 0 (Add@185)."""
        vs = E._videos('la1', '2099-01-01')
        # semilla mínima vía _STATE
        E._STATE['days'] = {'2099-01-01': {'la1': [
            {'title': 'A', 'desc': '', 'icon': '', 'start': 1, 'stop': 2}]}}
        vs = E._videos('la1', '2099-01-01')
        E._STATE['days'] = {}
        self.assertEqual(vs[0]['episode'], 1)


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


class TestEpgMatchE2E(unittest.TestCase):
    """EPG-001/002: matching canal<->programa validado de punta a punta —
    invariantes sobre el resultado de _build + la métrica expuesta."""

    def setUp(self):
        self._cfg = dict(E.CFG)
        self._open = E._OPENER.open
        self._pub = E._public_url
        E._public_url = lambda u: True
        E.CFG['epg_url'] = 'https://epg.test/x.xml'

    def tearDown(self):
        E.CFG.clear(); E.CFG.update(self._cfg)
        E._OPENER.open = self._open
        E._public_url = self._pub

    def _build(self, metas, progs, canales):
        import unittest.mock as um
        xml = _xmltv(progs, canales=canales)
        E._OPENER.open = lambda req, timeout=0: io.BytesIO(xml)
        with um.patch.object(E, '_log'), um.patch.object(E, 'log_ev'):
            return E._build(metas)

    def test_matching_end_to_end(self):
        """Cadena completa: XMLTV real-shape -> metas -> videos protocolares.
        tvg-id exacto, nombre normalizado y desconocido se clasifican bien."""
        import time as _t
        now = _t.time()
        dia = _t.strftime('%Y-%m-%d', _t.gmtime(now))
        metas = [{'id': 'm3u:1', 'tvg': 'la1.es', 'name': 'La 1'},
                 {'id': 'm3u:2', 'tvg': '', 'name': 'La Sexta'},      # match por nombre
                 {'id': 'm3u:3', 'tvg': 'cnn.int', 'name': 'CNN'}]   # sin match
        progs = [('la1.es', _ts(now), _ts(now + 60), 'T1', '', ''),
                 ('la1.es', _ts(now + 60), _ts(now + 120), 'T2', '', ''),
                 ('lasexta.es', _ts(now), _ts(now + 60), 'S1', '', ''),
                 ('otro.es', _ts(now), _ts(now + 60), 'X', '', '')]  # no mapeado
        out = self._build(metas, progs, ('la1.es', 'lasexta.es', 'otro.es'))
        self.assertIsNotNone(out)
        # invariante 1: solo canales mapeados tienen programas
        self.assertEqual(set(out['map']), {'m3u:1', 'm3u:2'})
        cids = {c for d in out['days'].values() for c in d}
        self.assertTrue(cids <= set(out['map']))
        # invariante 2: cada programa cae en el bucket de SU canal y día
        self.assertEqual([p['title'] for p in out['days'][dia]['m3u:1']],
                         ['T1', 'T2'])
        self.assertEqual([p['title'] for p in out['days'][dia]['m3u:2']], ['S1'])
        self.assertNotIn('m3u:3', cids)
        # invariante 3: videos emitidos apuntan a su canal, start<stop
        for cid in cids:
            for v in E._videos(cid, dia):
                self.assertTrue(v['id'].startswith(cid + ':'))
                self.assertLess(v['startTime'], v['endTime'])

    def test_stats_y_metrica(self):
        """EPG-002: stats coherentes con el matching + evento epg_match."""
        import time as _t
        import unittest.mock as um
        now = _t.time()
        metas = [{'id': 'm3u:1', 'tvg': 'la1.es', 'name': 'La 1'},
                 {'id': 'm3u:2', 'tvg': 'cnn.int', 'name': 'CNN'}]
        progs = [('la1.es', _ts(now), _ts(now + 60), 'T', '', '')]
        xml = _xmltv(progs, canales=('la1.es',))
        E._OPENER.open = lambda req, timeout=0: io.BytesIO(xml)
        with um.patch.object(E, '_log'), um.patch.object(E, 'log_ev') as ev:
            out = E._build(metas)
        st = out['stats']
        self.assertEqual(st['channels'], 2)
        self.assertEqual(st['mapped'], 1)
        self.assertEqual(st['with_programmes'], 1)
        self.assertEqual(st['programmes'], 1)
        ev.assert_called_once_with('epg_match', **st)

    def test_epg_stats_getter_y_metricas(self):
        """epg_stats() refleja _STATE; /metrics expone conflux_epg_match."""
        old = dict(E._STATE)
        try:
            E._STATE['stats'] = {'channels': 10, 'mapped': 8,
                                 'with_programmes': 6, 'programmes': 100}
            self.assertEqual(E.epg_stats()['with_programmes'], 6)
            from conflux.web.metrics import _metrics_text
            txt = _metrics_text()
            self.assertIn('conflux_epg_match{kind="with_programmes"} 6', txt)
        finally:
            E._STATE.clear(); E._STATE.update(old)
        E._STATE['stats'] = {}
        self.assertEqual(E.epg_stats(), {})


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
