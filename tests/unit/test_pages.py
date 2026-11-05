# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.pages — HTML admin/configure."""
import unittest
from unittest.mock import patch

import conflux.web.pages as pg


class TestPages(unittest.TestCase):
    def test_admin_html(self):
        h = pg._admin_page()
        self.assertIn('<', h)
        self.assertIn('html', h.lower())

    def test_configure_html(self):
        h = pg._configure_page()
        self.assertIn('<form', h)

    def test_health_page_json(self):
        d = pg._health_page()
        self.assertIn('sources', d)





class TestLandingPage(unittest.TestCase):
    def test_landing_host_loopback(self):
        h = pg._landing_page('127.0.0.1:9700', tls=False)
        self.assertIn('stremio://127.0.0.1:9700/manifest.json', h)
        self.assertIn('http://127.0.0.1:9700/manifest.json', h)

    def test_landing_host_lan(self):
        """La URL de instalación sigue al Host header: LAN/dominio también."""
        h = pg._landing_page('192.168.1.50:9700', tls=False)
        self.assertIn('stremio://192.168.1.50:9700/manifest.json', h)

    def test_landing_tls_https(self):
        h = pg._landing_page('addon.example.com', tls=True)
        self.assertIn('https://addon.example.com/manifest.json', h)
        self.assertNotIn('http://addon', h)


class TestCfgFormFields(unittest.TestCase):
    def test_checkboxes_ultimo_gana(self):
        """hidden 0 + checkbox 1 -> el último valor gana (patrón HTML form)."""
        cfg = {}
        pg._cfg_form_fields({'stream_prefetch': ['0', '1'],
                             'torrent_health': ['0']}, cfg)
        self.assertTrue(cfg['stream_prefetch'])
        self.assertFalse(cfg['torrent_health'])

    def test_stream_max_gb_int_y_basura(self):
        cfg = {}
        pg._cfg_form_fields({'stream_max_gb': ['4']}, cfg)
        self.assertEqual(cfg['stream_max_gb'], 4)
        pg._cfg_form_fields({'stream_max_gb': ['abc']}, cfg)
        self.assertEqual(cfg['stream_max_gb'], 4)      # basura no pisa el valor

    def test_csv_listas(self):
        cfg = {}
        pg._cfg_form_fields({'stream_sort': [' calidad , idioma ,, ']}, cfg)
        self.assertEqual(cfg['stream_sort'], ['calidad', 'idioma'])

    def test_secretos_solo_si_vienen(self):
        """Campo secreto vacío NO pisa el valor previo; con valor sí."""
        cfg = {'torznab_key': 'VIEJA'}
        pg._cfg_form_fields({'torznab_key': ['  ']}, cfg)
        self.assertEqual(cfg['torznab_key'], 'VIEJA')
        pg._cfg_form_fields({'torznab_key': ['NUEVA']}, cfg)
        self.assertEqual(cfg['torznab_key'], 'NUEVA')

    def test_stream_langs_vacio_conserva(self):
        cfg = {'stream_langs': ['es']}
        with patch.dict(pg.CFG, {'stream_langs': ['es', 'en']}):
            pg._cfg_form_fields({'stream_langs': ['']}, cfg)
        self.assertEqual(cfg['stream_langs'], ['es', 'en'])  # fallback CFG


class TestCsvList(unittest.TestCase):
    def test_limpia_y_descarta_vacios(self):
        self.assertEqual(pg._csv_list({'k': [' a ,,b , ']}, 'k'), ['a', 'b'])
        self.assertEqual(pg._csv_list({}, 'k'), [])


class _Snap:
    """Snapshot+restore de dicts globales de estado."""
    def __init__(self, *ds):
        self._ds = ds
        self._snap = [dict(d) for d in ds]

    def restore(self):
        for d, s in zip(self._ds, self._snap):
            d.clear()
            d.update(s)


class TestHealthPageDatos(unittest.TestCase):
    def test_cd_min_solo_activo_y_latencias(self):
        """cd_min/cd_motivo solo si n in COOLDOWN y no expirado (In/Gt/And@34,
        Sub/Div/60@33); avg y pcts con valores reales (Div@38, .5/.95/.99)."""
        now = __import__('time').time()
        snap = _Snap(pg.HEALTH, pg.COOLDOWN, pg._fails, pg._rtime, pg._rts)
        try:
            h = {'ok': 1, 'fail': 0, 'last': 0, 'err': '', 'n': 0, 'streak': 0}
            pg.HEALTH['A'] = dict(h)
            pg.HEALTH['B'] = dict(h)
            pg.HEALTH['C'] = dict(h)
            pg.COOLDOWN['A'] = (now + 690, 'cuota')    # activo: cd_min=12
            pg.COOLDOWN['B'] = (now - 5, 'viejo')      # expirado: sin campos
            pg._fails.clear()                          # strikes reales de otros tests
            pg._fails['dead'] = (3, now)
            pg._rtime['/x'] = (3, 100.0, 50.0)
            pg._rts['/x'] = [10, 20, 30, 40]
            d = pg._health_page()
        finally:
            snap.restore()
        self.assertEqual(d['sources']['A']['cd_min'], 12)      # 690/60 redondeado
        self.assertEqual(d['sources']['A']['cd_motivo'], 'cuota')
        self.assertNotIn('cd_min', d['sources']['B'])          # expirado -> plano
        self.assertNotIn('cd_min', d['sources']['C'])          # sin cooldown
        self.assertEqual(d['fetch_fails'], {'dead': 3})
        lat = d['latency_ms']['/x']
        self.assertEqual(lat['avg'], 33.3)
        self.assertEqual(lat['p50'], 30)
        self.assertEqual(lat['p95'], 40)
        self.assertEqual(lat['p99'], 40)

    def test_cd_borde_exacto_expira(self):
        """cd[0] == now -> ya expirado: > no >= (Gt@34, freezegun)."""
        from freezegun import freeze_time
        snap = _Snap(pg.HEALTH, pg.COOLDOWN)
        try:
            with freeze_time('2025-01-01 12:00:00'):
                pg.HEALTH['A'] = {'ok': 1, 'fail': 0, 'last': 0,
                                  'err': '', 'n': 0, 'streak': 0}
                pg.COOLDOWN['A'] = (__import__('time').time(), 'x')
                d = pg._health_page()
        finally:
            snap.restore()
        self.assertNotIn('cd_min', d['sources']['A'])


class TestManifestReply(unittest.TestCase):
    def test_thread_daemon(self):
        """Prefetch se lanza daemon=True (True@47: no-daemon bloquea cierre)."""
        prof = type('P', (), {'name': ''})()
        with patch.object(pg.threading, 'Thread') as th, \
                patch.object(pg, 'build_manifest', return_value={}), \
                patch.object(pg, '_PROF', prof):
            pg._manifest_reply()
        self.assertTrue(th.call_args[1].get('daemon'))

    def test_id_perfil(self):
        with patch.object(pg.threading, 'Thread'), \
                patch.object(pg, 'build_manifest', return_value={}), \
                patch.object(pg, '_PROF') as prof:
            prof.name = 'kids'
            mf = pg._manifest_reply()
        self.assertEqual(mf['id'], 'local.uniform.streams.kids')
        self.assertIn('kids', mf['name'])


class TestCfgScalarClamps(unittest.TestCase):
    def test_nums_acotados(self):
        """_CFG_NUM: lo/hi reales (64+1, 8+1, 512+1, 65535+1)."""
        cfg = {}
        pg._cfg_form_fields({'nntp_port': ['70000']}, cfg)
        self.assertEqual(cfg['nntp_port'], 65535)
        pg._cfg_form_fields({'nntp_port': ['0']}, cfg)
        self.assertEqual(cfg['nntp_port'], 1)
        pg._cfg_form_fields({'nntp_connections': ['200']}, cfg)
        self.assertEqual(cfg['nntp_connections'], 64)
        pg._cfg_form_fields({'max_connections': ['900']}, cfg)
        self.assertEqual(cfg['max_connections'], 512)
        pg._cfg_form_fields({'max_connections': ['2']}, cfg)
        self.assertEqual(cfg['max_connections'], 8)

class TestSrcCheckbox(unittest.TestCase):
    def test_src_ausente_no_desactiva(self):
        """'src' fuera del form no produce sources_disabled (In@137 -> notin
        desactivaría todo)."""
        cfg = {}
        pg._cfg_form_fields({'stream_sort': ['res']}, cfg)
        self.assertNotIn('sources_disabled', cfg)

    def test_src_presente_desactiva_restos(self):
        """src=['A'] -> disabled = todo menos A (NotIn@139)."""
        from conflux.core.state import Source
        with patch.object(pg, 'SOURCES',
                          [Source('A', 'u1'), Source('B', 'u2'),
                           Source('C', 'u3')]):
            cfg = {}
            pg._cfg_form_fields({'src': ['A', 'C']}, cfg)
        self.assertEqual(cfg['sources_disabled'], ['B'])


class TestConfigureSave(unittest.TestCase):
    def test_campos_presentes_pisan(self):
        """mdblist_key/debrid_provider/m3u_path en form -> CFG (In@146/150/154)."""
        old = dict(pg.CFG)
        try:
            with patch.object(pg, '_save_cfg'), patch.object(pg, 'load_config'):
                pg._configure_save({'mdblist_key': [' k1 '],
                                    'debrid_provider': ['torbox'],
                                    'm3u_path': ['/x/canales.m3u'],
                                    'debrid_key': ['K']})
            self.assertEqual(pg.CFG['mdblist_key'], 'k1')
            self.assertEqual(pg.CFG['debrid_provider'], 'torbox')
            self.assertEqual(pg.CFG['m3u_path'], '/x/canales.m3u')
            self.assertEqual(pg.CFG['debrid_key'], 'K')
        finally:
            pg.CFG.clear()
            pg.CFG.update(old)


class TestAdminPageDatos(unittest.TestCase):
    def test_fila_cooldown_ultimo_err_y_res(self):
        """cd 'X min · motivo', last '5min', err[:28], res top4 desc
        (Gt/Sub/60/Div@217, 4+1@219, Sub/60/Div@221, 28+1@223)."""
        now = __import__('time').time()
        snap = _Snap(pg.HEALTH, pg.COOLDOWN, pg._SERVED, pg._rtime, pg._rts,
                     pg._fails)
        try:
            pg.HEALTH['A'] = {'ok': 3, 'fail': 1, 'last': now - 300,
                              'err': 'E' * 30, 'n': 0, 'streak': 0}
            pg.COOLDOWN['A'] = (now + 600, 'cuota')
            pg._SERVED['A'] = {'n': 7, 'res': {'1080p': 5, '720p': 2, '4K': 1,
                                               'SD': 1, 'CAM': 1}}
            pg._rtime['/x'] = (3, 90.0, 50.0)
            pg._rts['/x'] = [10, 20, 30]
            pg._fails['h'] = (3, now)
            html = pg._admin_page()
        finally:
            snap.restore()
        self.assertIn('10 min · cuota', html)
        self.assertIn('5min', html)
        self.assertIn('E' * 28, html)
        self.assertNotIn('E' * 29, html)
        self.assertIn('1080p:5', html)
        self.assertNotIn('CAM:1', html)                    # top 4 de 5
        self.assertIn('h: 3', html)

    def test_fails_vacio_guion(self):
        """Sin fails -> '—' (Or@237 -> and daría el join, no el guion)."""
        snap = _Snap(pg._fails)
        try:
            pg._fails.clear()
            html = pg._admin_page()
        finally:
            snap.restore()
        self.assertIn('—', html)


class TestHtmlDefaults(unittest.TestCase):
    """Los defaults de CFG.get(...) solo difieren con la clave AUSENTE."""
    def _pop(self, *ks):
        old = {k: pg.CFG[k] for k in ks if k in pg.CFG}
        for k in ks:
            pg.CFG.pop(k, None)
        return old

    def test_defaults_numericos(self):
        """max_connections 64, nntp_port 563, nntp_connections 5
        (mutantes +1 solo difieren sin la clave)."""
        old = self._pop('max_connections', 'nntp_port', 'nntp_connections')
        try:
            h = pg._configure_page()
        finally:
            pg.CFG.update(old)
        self.assertIn("value='64'", h)
        self.assertIn("value='563'", h)
        self.assertIn("value='5'", h)

    def test_defaults_bool_checked(self):
        """stream_prefetch/torrent_health/nntp_ssl default True -> checked
        (True@279/294/320)."""
        old = self._pop('stream_prefetch', 'torrent_health', 'nntp_ssl')
        try:
            h = pg._configure_page()
        finally:
            pg.CFG.update(old)
        self.assertIn("name='stream_prefetch' value='1' checked", h)
        self.assertIn("name='torrent_health' value='1' checked", h)
        self.assertIn("name='nntp_ssl' value='1' checked", h)

class TestHealthJsonPcts(unittest.TestCase):
    def test_pcts_con_200_muestras(self):
        """p50/p95/p99 con lista grande -> mutantes .5/.95/.99+1 cambian el índice
        (min(len-1, int(n*q)) — 0.99*200 ya da el último, +1 solo difiere si hay >1)."""
        snap = _Snap(pg._rtime, pg._rts, pg.HEALTH, pg.COOLDOWN, pg._fails)
        try:
            pg._rtime['/r'] = (4, 200.0, 80.0)
            pg._rts['/r'] = list(range(200))
            d = pg._health_page()
        finally:
            snap.restore()
        lat = d['latency_ms']['/r']
        self.assertEqual(lat['p50'], 100)   # s[int(200*.5)]  = s[100]
        self.assertEqual(lat['p95'], 190)   # s[int(200*.95)] = s[190] (mutante: s[199])
        self.assertEqual(lat['p99'], 198)   # s[int(200*.99)] = s[198] (mutante: s[199])
        self.assertEqual(lat['avg'], 50.0)
        self.assertEqual(lat['max'], 80.0)


class TestAdminLatenciaYCooldown(unittest.TestCase):
    def test_fila_latencia_y_minutos(self):
        """Fila HTML: avg/p50/p95/p99/max + '2 min' de cooldown restante y
        '2min' desde el último ok (Div@227, .5/.95/.99+1@227-228, 60+1@217/221)."""
        now = __import__('time').time()
        snap = _Snap(pg.HEALTH, pg.COOLDOWN, pg._SERVED, pg._rtime, pg._rts,
                     pg._fails)
        try:
            pg.HEALTH['A'] = {'ok': 1, 'fail': 0, 'last': now - 91,
                              'err': '', 'n': 0, 'streak': 0}
            pg.COOLDOWN['A'] = (now + 91, 'cuota')     # 91/60 -> '2 min'; /61 -> '1'
            pg._SERVED['A'] = {'n': 0, 'res': {}}
            pg._rtime['/r'] = (4, 200.0, 80.0)
            pg._rts['/r'] = list(range(200))
            html = pg._admin_page()
        finally:
            snap.restore()
        self.assertIn('2 min · cuota', html)           # Gt@217/60+1: '2', no '1'
        self.assertIn('2min', html)                    # 60+1@221
        for cell in ('>50<', '>100<', '>190<', '>198<', '>80<'):
            self.assertIn(cell, html)                  # avg/p50/p95/p99/max

    def test_cd_expirado_punto(self):
        """cd[0] <= now -> '·' (Gt@217: >= mostraría '0 min')."""
        from freezegun import freeze_time
        snap = _Snap(pg.HEALTH, pg.COOLDOWN, pg._SERVED)
        try:
            with freeze_time('2025-06-01 10:00:00'):
                t = __import__('time').time()
                pg.HEALTH['A'] = {'ok': 1, 'fail': 0, 'last': 0,
                                  'err': '', 'n': 0, 'streak': 0}
                pg.COOLDOWN['A'] = (t, 'x')
                pg._SERVED['A'] = {'n': 0, 'res': {}}
                html = pg._admin_page()
        finally:
            snap.restore()
        self.assertNotIn('0 min · x', html)


class TestCfgDictFields(unittest.TestCase):
    def test_gate_sin_clave_no_toca(self):
        """Sin 'debrid_providers'/'source_groups' en form -> no aparecen en cfg
        (In@85/89: notin pisaría con dict vacío)."""
        cfg = {'debrid_providers': {'rd': 'k9'}, 'source_groups': {'m': ['A']}}
        pg._cfg_dict_fields({'otra': ['x']}, cfg)
        self.assertEqual(cfg['debrid_providers'], {'rd': 'k9'})
        self.assertEqual(cfg['source_groups'], {'m': ['A']})

    def test_debrid_vacio_conserva_previo(self):
        """`v or prev.get(p,'')`: valor vacío en textarea conserva el secret
        previo (Or@87); `or {}` con clave ausente en CFG no crashea (Or@86)."""
        old = {k: pg.CFG[k] for k in ('debrid_providers',) if k in pg.CFG}
        pg.CFG.pop('debrid_providers', None)
        try:
            cfg = {}
            pg._cfg_dict_fields({'debrid_providers': ['tb=k1\nrd=']}, cfg)
            self.assertEqual(cfg['debrid_providers'], {'tb': 'k1', 'rd': ''})
            with patch.dict(pg.CFG, {'debrid_providers': {'rd': 'k9'}}):
                cfg2 = {}
                pg._cfg_dict_fields({'debrid_providers': ['tb=k1\nrd=']}, cfg2)
            self.assertEqual(cfg2['debrid_providers']['rd'], 'k9')
        finally:
            pg.CFG['debrid_providers'] = old.get('debrid_providers', {})

    def test_source_groups_parse(self):
        cfg = {}
        pg._cfg_dict_fields({'source_groups': ['movie= A , B \n series=C']}, cfg)
        self.assertEqual(cfg['source_groups'], {'movie': ['A', 'B'],
                                                'series': ['C']})


class TestConfigurePageHtml(unittest.TestCase):
    def test_textareas_newlines(self):
        """chr(10).join de regex (10+1@263/265 -> chr(11)); or->and vacía el
        textarea pese a haber patrones (Or@265)."""
        with patch.dict(pg.CFG, {'stream_exclude_regex': ['cam', 'hdts'],
                                 'stream_required_regex': ['es', 'lat']}):
            h = pg._configure_page()
        self.assertIn('cam\nhdts', h)
        self.assertIn('es\nlat', h)

    def test_label_4s_defecto(self):
        """'(0 = 4s por defecto)' literal (4+1@272)."""
        self.assertIn('4s por defecto', pg._configure_page())

    def test_src_checked_respeta_disabled(self):
        """Checkbox checked solo si name no está en _DISABLED (NotIn@351)."""
        from conflux.core.state import Source
        with patch.object(pg, 'SOURCES', [Source('A', 'u1'), Source('B', 'u2')]), \
                patch.object(pg, '_DISABLED', {'B'}):
            h = pg._configure_page()
        self.assertRegex(h, r"name='src' value='A' checked")
        self.assertRegex(h, r"name='src' value='B'(?! checked)")

    def test_langs_csv_value(self):
        """stream_langs une con ',' (Or@353: and -> '[]' vacío)."""
        with patch.dict(pg.CFG, {'stream_langs': ['es', 'en']}):
            h = pg._configure_page()
        self.assertIn("name='stream_langs' value='es,en'", h)

    def test_debrid_select_selected(self):
        """option selected solo si ==provider (Eq@354); label v o '· ninguno ·'
        (Or@354: and mostraría '· ninguno ·' como label de 'torbox')."""
        with patch.dict(pg.CFG, {'debrid_provider': 'torbox'}):
            h = pg._configure_page()
        self.assertIn("value='torbox' selected>torbox", h)
        self.assertRegex(h, r"value=''>[^<]*ninguno[^<]*<")

    def test_csv_campos_valores(self):
        """extra_sources/catalogs_disabled/source_order join ',' en value
        (Or@390/392/400)."""
        with patch.dict(pg.CFG, {'extra_sources': ['https://a'],
                                 'catalogs_disabled': ['c1'],
                                 'source_order': ['X', 'Y']}):
            h = pg._configure_page()
        self.assertIn("name='extra_sources' value='https://a'", h)
        self.assertIn("name='catalogs_disabled' value='c1'", h)
        self.assertIn("name='source_order' value='X,Y'", h)


class TestConfigurePageRender(unittest.TestCase):
    def test_or_listas_renderizan(self):
        """Cada `CFG.get(k) or []/{}` alimenta un campo (mutantes and ->
        campo vacío pese a haber valores)."""
        with patch.dict(pg.CFG, {
                'debrid_providers': {'tb': 'k1', 'rd': 'k2'},
                'stream_exclude': ['cam'],
                'stream_exclude_regex': ['(?i)cam'],
                'stream_required_regex': ['es'],
                'stream_required': ['es'],
                'stream_sort': ['res'],
                'stream_preferred': ['remux'],
                'scrapers': ['yts'],
                'scraper_rss': ['http://f/r'],
                'source_groups': {'movie': ['A', 'B']}}):
            h = pg._configure_page()
            sg = pg._sg_text()
        self.assertIn('tb=\nrd=', h)                # chr(10) join
        self.assertIn("value='cam'", h)
        self.assertIn('(?i)cam', h)
        self.assertIn("value='es'", h)
        self.assertIn("value='res'", h)
        self.assertIn("value='remux'", h)
        self.assertIn("value='yts'", h)
        self.assertIn('http://f/r', h)
        self.assertEqual(sg, 'movie=A,B')


if __name__ == '__main__':
    unittest.main()
