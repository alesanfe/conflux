# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.core.state — cachés, health, mutación in situ."""
import os
import time
import unittest

import conflux.core.state as st


class TestCache(unittest.TestCase):
    def setUp(self):
        self.c = {}

    def test_cget_miss(self):
        self.assertIsNone(st._cget(self.c, 'k'))

    def test_cset_cget_hit(self):
        st._cset(self.c, 'k', {'a': 1})
        self.assertEqual(st._cget(self.c, 'k'), {'a': 1})

    def test_cget_expira(self):
        self.c['k'] = (time.time() - st.CACHE_TTL - 1, 'v')
        self.assertIsNone(st._cget(self.c, 'k'))

    def test_cstale_sobrevive_ttl(self):
        self.c['k'] = (time.time() - st.CACHE_TTL - 1, 'v')
        self.assertEqual(st._cstale(self.c, 'k'), 'v')

    def test_cstale_muere_a_24h(self):
        self.c['k'] = (time.time() - st.STALE_MAX - 1, 'v')
        self.assertIsNone(st._cstale(self.c, 'k'))

    def test_cset_evict_fifo(self):
        for i in range(410):
            st._cset(self.c, i, i)
        self.assertLessEqual(len(self.c), 401)


class TestHealth(unittest.TestCase):
    def test_health_registra(self):
        st._health('T', True, '', 5)
        h = st.HEALTH['T']
        self.assertEqual(h['ok'], 1)
        self.assertEqual(h['n'], 5)

    def test_health_streak_fallos(self):
        st._health('T2', False, 'err', 0)
        st._health('T2', False, 'err', 0)
        self.assertEqual(st.HEALTH['T2']['streak'], 2)
        st._health('T2', True, '', 0)
        self.assertEqual(st.HEALTH['T2']['streak'], 0)


class TestCfgDefaults(unittest.TestCase):
    def test_defaults_cargados(self):
        for k in ('stream_max_gb', 'stream_langs'):
            self.assertIn(k, st.CFG)

    def test_defaults_contrato(self):
        """Los defaults son el contrato del producto (mutantes const flip)."""
        d = st._CFG_DEFAULTS
        self.assertNotIn('calendar_months_ahead', d)   # feature retirada
        self.assertNotIn('trakt_client_id', d)         # OAuth retirado
        self.assertTrue(d['stream_prefetch'])
        self.assertFalse(d['debrid_proxy'])
        self.assertFalse(d['parental'])
        self.assertTrue(d['mediafusion'])
        self.assertTrue(d['torrent_health'])
        self.assertEqual(d['nntp_port'], 563)
        self.assertTrue(d['nntp_ssl'])
        self.assertEqual(d['nntp_connections'], 5)
        self.assertEqual(d['max_connections'], 64)


class TestConstantes(unittest.TestCase):
    """Contrato operativo documentado en AGENTS.md — un mutante aquí cambia
    el comportamiento global (disyuntor, cachés, cooldowns)."""

    def test_tiempos_y_topes(self):
        self.assertEqual(st.STREAM_TIMEOUT, 4)
        self.assertEqual(st.STREAM_BUDGET, 3.2)
        self.assertEqual(st.CACHE_TTL, 600)
        self.assertEqual(st.PORT, 9700)
        self.assertEqual(st.HOST_OPEN, 4)
        self.assertEqual(st.HOST_CD, 60)
        self.assertEqual(st.COOLDOWN_T, 12 * 3600)
        self.assertEqual(st.FAIL_CD_T, 15 * 60)
        self.assertEqual(st.FAIL_STREAK, 3)
        self.assertEqual(st.STALE_MAX, 24 * 3600)

    def test_pools(self):
        self.assertEqual(st._POOL._max_workers, 32)
        self.assertEqual(st._POOL_BG._max_workers, 12)

    def test_years_descendente(self):
        """_YEARS = [año_actual .. año-11] (Sub@261 / 12+1@261)."""
        y = time.gmtime().tm_year
        self.assertEqual(st._YEARS[0], str(y))
        self.assertEqual(len(st._YEARS), 12)
        self.assertEqual(st._YEARS[-1], str(y - 11))

    def test_log_rotacion(self):
        """RotatingFileHandler en el root logger: 5MB max, 5 backups (CONFLUX-017)."""
        import logging.handlers
        rot = [h for h in logging.getLogger().handlers
               if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertTrue(rot)
        self.assertEqual(rot[0].maxBytes, st.LOG_MAX_BYTES)
        self.assertEqual(rot[0].backupCount, st.LOG_BACKUPS)


class TestPct(unittest.TestCase):
    def test_vacio_0(self):
        self.assertEqual(st._pct([], 0.5), 0)                    # not-elim@203

    def test_mediana(self):
        """_pct([0..9], .5) -> s[int(10*.5)] = s[5] (Mult@206 -> último)."""
        self.assertEqual(st._pct(list(range(10)), 0.5), 5)

    def test_q1_es_max(self):
        """q=1 -> último (Sub@206: len+1 -> IndexError)."""
        self.assertEqual(st._pct(list(range(10)), 1.0), 9)


class TestCacheBordes(unittest.TestCase):
    def test_cset_no_evict_a_400(self):
        """len==400 no poda: la guarda es > no >= (Gt@225)."""
        c = {}
        for i in range(400):
            st._cset(c, i, i)
        self.assertEqual(len(c), 400)

    def test_cset_len_400_mas_uno_sin_poda(self):
        """Insertar sobre len==400 tampoco poda (401 finales; el mutante
        >= podaría aquí -> 301)."""
        c = {}
        for i in range(401):
            st._cset(c, i, i)
        self.assertEqual(len(c), 401)
        self.assertIn(0, c)

    def test_cset_evict_exacto(self):
        """Al superar 400 poda 100 más antiguos (400+1@225, 100+1@226)."""
        c = {}
        for i in range(402):
            st._cset(c, i, i)
        self.assertEqual(len(c), 302)          # evict 100 al insertar el 402º
        self.assertNotIn(0, c)                 # FIFO: los primeros se van
        self.assertIn(401, c)

    def test_cget_borde_ttl(self):
        """edad == CACHE_TTL -> miss: < no <= (Lt@233, freezegun)."""
        from freezegun import freeze_time
        c = {}
        with freeze_time('2025-01-01 12:00:00'):
            c['k'] = (time.time() - st.CACHE_TTL, 'v')
            self.assertIsNone(st._cget(c, 'k'))
            c['k2'] = (time.time() - st.CACHE_TTL + 1, 'v')
            self.assertEqual(st._cget(c, 'k2'), 'v')

    def test_cstale_borde_24h(self):
        """edad == STALE_MAX -> None: < no <= (Lt@259, freezegun)."""
        from freezegun import freeze_time
        c = {}
        with freeze_time('2025-01-01 12:00:00'):
            c['k'] = (time.time() - st.STALE_MAX, 'v')
            self.assertIsNone(st._cstale(c, 'k'))
            c['k2'] = (time.time() - st.STALE_MAX + 1, 'v')
            self.assertEqual(st._cstale(c, 'k2'), 'v')


class TestHealthStreak(unittest.TestCase):
    def test_streak_cuenta_y_reset(self):
        """streak = fails consecutivos (Add@220: -1 iría a negativo)."""
        st._health('TS', False)
        st._health('TS', False)
        self.assertEqual(st.HEALTH['TS']['streak'], 2)
        st._health('TS', False)
        self.assertEqual(st.HEALTH['TS']['streak'], 3)
        st._health('TS', True)
        self.assertEqual(st.HEALTH['TS']['streak'], 0)
        self.assertEqual(st.HEALTH['TS']['fail'], 3)


class TestMetaTitle(unittest.TestCase):
    def setUp(self):
        self._old = dict(st._meta_c)
        st._meta_c.clear()

    def tearDown(self):
        st._meta_c.clear()
        st._meta_c.update(self._old)

    def _seed(self, meta):
        st._meta_c[('movie', 'tt1')] = (time.time(), {'meta': meta})
        st._meta_c[('series', 'tt1')] = (time.time(), {'meta': meta})

    def test_sin_meta(self):
        self.assertEqual(st._meta_title('movie', 'tt9'), ('tt9', '', None))  # Or@239/240

    def test_name_preferido_a_title(self):
        self._seed({'name': 'N', 'title': 'T'})
        self.assertEqual(st._meta_title('movie', 'tt1')[1], 'N')           # Or@240

    def test_title_fallback(self):
        self._seed({'title': 'T'})
        self.assertEqual(st._meta_title('movie', 'tt1')[1], 'T')

    def test_se_formato_2d(self):
        self._seed({})
        self.assertEqual(st._meta_title('series', 'tt1:1:2')[2], 's01e02')  # 2+1@244

    def test_se_requiere_3_segmentos(self):
        self._seed({})
        self.assertIsNone(st._meta_title('series', 'tt1:1')[2])            # 3+1@243

    def test_se_segmento_extra_ignorado(self):
        """parts[1:3] exacto: un 4º segmento no entra en el isdigit (3+1@243)."""
        self._seed({})
        self.assertEqual(st._meta_title('series', 'tt1:1:2:x')[2], 's01e02')

    def test_se_no_digit_none(self):
        self._seed({})
        self.assertIsNone(st._meta_title('series', 'tt1:a:2')[2])          # And/GtE@243


class TestOpen(unittest.TestCase):
    def test_binario_sin_encoding(self):
        """Modo 'rb' no debe recibir encoding=utf-8 (NotIn@35 -> ValueError)."""
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.bin')
            st._open(p, 'wb').write(b'\xff\x00')
            self.assertEqual(st._open(p, 'rb').read(), b'\xff\x00')

    def test_texto_utf8_por_defecto(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.txt')
            st._open(p, 'w').write('ña')
            self.assertEqual(st._open(p).read(), 'ña')


class TestAtomicJson(unittest.TestCase):
    def test_escribe_y_no_deja_tmp(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c.json')
            st._atomic_json(p, {'a': 1})
            self.assertFalse(os.path.exists(p + '.tmp'))
            with open(p, encoding='utf-8') as fh:
                self.assertEqual(json.load(fh), {'a': 1})

    def test_sobrescribe_atomico(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c.json')
            st._atomic_json(p, {'a': 1})
            st._atomic_json(p, {'a': 2})
            with open(p, encoding='utf-8') as fh:
                self.assertEqual(json.load(fh), {'a': 2})

    def test_dirsync_oserror_tragado(self):
        """fsync de dir no soportado (algunos fs/win32) -> se ignora."""
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'c.json')
            with mock.patch.object(st.os, 'open',
                                   side_effect=OSError('no fsync dir')):
                st._atomic_json(p, {'a': 1})       # no propaga
            self.assertTrue(os.path.exists(p))


class TestClock(unittest.TestCase):
    def tearDown(self):
        st.CLOCK.reset()

    def test_fake_advance_y_monotonic(self):
        fc = st.CLOCK.fake(100.0)
        self.assertEqual(fc.time(), 100.0)
        self.assertEqual(fc.monotonic(), 100.0)
        fc.advance(5)
        self.assertEqual(fc.time(), 105.0)
        self.assertEqual(st.CLOCK.time(), 105.0)
        self.assertEqual(st.CLOCK.monotonic(), 105.0)

    def test_reset_vuelve_a_real(self):
        st.CLOCK.fake(0.0)
        st.CLOCK.reset()
        self.assertGreater(st.CLOCK.time(), 1_000_000)
        self.assertGreater(st.CLOCK.monotonic(), 0)

    def test_set_clock_custom(self):
        fake = st.FakeClock(7.0)
        st.CLOCK.set(fake)
        self.assertIs(st.CLOCK._d, fake)
        self.assertEqual(st.CLOCK.time(), 7.0)


class TestRedactFilter(unittest.TestCase):
    def test_excepcion_no_bloquea(self):
        """getMessage que explota -> filter devuelve True igualmente."""
        import logging
        from unittest import mock
        rec = logging.LogRecord('n', logging.INFO, 'p', 1, 'm', (), None)
        with mock.patch.object(rec, 'getMessage',
                               side_effect=RuntimeError('boom')):
            self.assertTrue(st._RedactFilter().filter(rec))


class TestLogLevel(unittest.TestCase):
    def test_cfg_log_level_fallback(self):
        import logging
        _, undo = _cfg_swap({'log_level': 'debug'})
        try:
            env = dict(os.environ)
            os.environ.pop('CONFLUX_LOG_LEVEL', None)
            try:
                self.assertEqual(st._log_level(), logging.DEBUG)
            finally:
                os.environ.clear()
                os.environ.update(env)
        finally:
            undo()

    def test_env_gana_a_cfg(self):
        import logging
        _, undo = _cfg_swap({'log_level': 'debug'})
        try:
            env = dict(os.environ)
            os.environ['CONFLUX_LOG_LEVEL'] = 'WARNING'
            try:
                self.assertEqual(st._log_level(), logging.WARNING)
            finally:
                os.environ.clear()
                os.environ.update(env)
        finally:
            undo()

    def test_set_log_level_valido_e_invalido(self):
        import logging
        root = logging.getLogger()
        old = root.level
        try:
            self.assertEqual(st.set_log_level('debug'), logging.DEBUG)
            self.assertEqual(root.level, logging.DEBUG)
            with self.assertRaises(ValueError):
                st.set_log_level('INEXISTENTE')
        finally:
            root.setLevel(old)


class TestSetupLogging(unittest.TestCase):
    def _sin_handlers_conflux(self):
        import logging
        root = logging.getLogger()
        saved = list(root.handlers)
        root.handlers = [h for h in root.handlers
                         if not getattr(h, '_conflux', False)]
        return root, saved

    def test_idempotente_no_duplica(self):
        import logging
        root = logging.getLogger()
        n = sum(1 for h in root.handlers if getattr(h, '_conflux', False))
        st._setup_logging()
        n2 = sum(1 for h in root.handlers if getattr(h, '_conflux', False))
        self.assertEqual(n, n2)                  # ya había: no añade

    def test_stderr_none_y_logfile_roto_nulllhandler(self):
        import logging
        from unittest import mock
        root, saved = self._sin_handlers_conflux()
        try:
            with mock.patch.object(st.sys, 'stderr', None), \
                 mock.patch.object(st.os, 'makedirs',
                                   side_effect=OSError('ro')):
                st._setup_logging()
            nuevos = [h for h in root.handlers
                      if getattr(h, '_conflux', False)]
            self.assertTrue(any(isinstance(h, logging.NullHandler)
                                for h in nuevos))
        finally:
            for h in list(root.handlers):
                if getattr(h, '_conflux', False) and h not in saved:
                    root.removeHandler(h)
            for h in saved:
                if h not in root.handlers:
                    root.addHandler(h)


class TestSubmitCtxYReqId(unittest.TestCase):
    def test_submit_ctx_propaga_req_id(self):
        import concurrent.futures as cf
        st.new_req_id()
        rid = st._REQ_ID.get()
        with cf.ThreadPoolExecutor(1) as pool:
            fut = st.submit_ctx(pool, lambda: st._REQ_ID.get())
            self.assertEqual(fut.result(), rid)

    def test_new_req_id_8_hex(self):
        rid = st.new_req_id()
        self.assertEqual(len(rid), 8)
        int(rid, 16)


class TestSourcesJsonModule(unittest.TestCase):
    """sources.json corrupto al importar -> warning + lista embebida.

    El path es código module-level: solo corre de verdad en un import
    fresco — subproceso con sys.frozen apuntando a un dir con el json roto."""

    def test_sources_corrupto_avisa_y_usa_embebida(self):
        import os
        import subprocess
        import sys
        import tempfile
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'sources.json'), 'w') as fh:
                fh.write('{roto')
            exe = os.path.join(d, 'conflux.exe')
            with open(exe, 'wb') as fh:
                fh.write(b'x')
            code = (
                "import sys; sys.frozen=True; "
                f"sys.executable={exe!r}; "
                f"sys.path.insert(0, {os.path.join(root, 'src')!r}); "
                "import logging, io; "
                "buf=io.StringIO(); "
                "h=logging.StreamHandler(buf); logging.getLogger().addHandler(h); "
                "import conflux.core.state as st; "
                "print('SOURCES:', len(st.SOURCES)); "
                "print('WARN:', 'ignorado' in buf.getvalue())")
            env = dict(os.environ)
            env['CONFLUX_LOG'] = os.path.join(d, 't.log')
            r = subprocess.run([sys.executable, '-c', code],
                               capture_output=True, text=True, timeout=60,
                               env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn('SOURCES: 0' if False else 'SOURCES:', r.stdout)
        self.assertIn('WARN: True', r.stdout)
        self.assertNotIn('SOURCES: 0', r.stdout)


def _cfg_swap(kv):
    import conflux.core.state as _st
    old = dict(_st.CFG)
    _st.CFG.update(kv)
    def restore():
        _st.CFG.clear()
        _st.CFG.update(old)
    return old, restore


if __name__ == '__main__':
    unittest.main()
