# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.app.meta — /meta routing."""
import unittest
from unittest.mock import patch

import conflux.app.meta as M
import conflux.core.state as st


class TestMetaTry(unittest.TestCase):
    def test_series_fallback_movie(self):
        self.assertEqual(M._meta_try('series'), ('series', 'movie'))  # orden: propio primero

    def test_movie_sin_fallback(self):
        self.assertEqual(M._meta_try('movie'), ('movie',))


class TestMetaOne(unittest.TestCase):
    def test_manifest_sin_meta_skip(self):
        with patch.dict(M.__dict__, {'manifests': [{'resources': [{'name': 'stream'}]}]}):
            self.assertIsNone(M._meta_one(0, 'movie', 'tt1'))

    def test_manifest_sin_meta_no_fetch(self):
        """Manifest sin recurso 'meta' -> None sin tocar la red (NotIn@37)."""
        src = type('S', (), {'base': 'http://src'})()
        with patch.dict(M.__dict__, {'manifests': [{'resources': [{'name': 'stream'}]}],
                                     'SOURCES': [src]}), \
                patch.object(M, 'fetch') as f:
            self.assertIsNone(M._meta_one(0, 'movie', 'tt1'))
        f.assert_not_called()

    def test_tipo_no_declarado_skip(self):
        m = {'resources': [{'name': 'meta', 'types': ['movie']}]}
        with patch.dict(M.__dict__, {'manifests': [m]}):
            self.assertIsNone(M._meta_one(0, 'series', 'tt1'))

    def test_tipo_no_declarado_no_fetch(self):
        """types=['movie'] + want=series -> None sin fetch (NotIn@40)."""
        m = {'resources': [{'name': 'meta', 'types': ['movie']}]}
        src = type('S', (), {'base': 'http://src'})()
        with patch.dict(M.__dict__, {'manifests': [m], 'SOURCES': [src]}), \
                patch.object(M, 'fetch') as f:
            self.assertIsNone(M._meta_one(0, 'series', 'tt1'))
        f.assert_not_called()

    def test_fetch_timeout_4(self):
        """meta upstream se pide con timeout=4 (mutante 4+1@42)."""
        src = type('S', (), {'base': 'http://src'})()
        with patch.dict(M.__dict__, {'manifests': [{}], 'SOURCES': [src]}), \
                patch.object(M, 'fetch', return_value={'meta': {'id': 'x'}}) as f:
            M._meta_one(0, 'movie', 'tt1')
        self.assertEqual(f.call_args[0][1], 4)


class TestMetaRace(unittest.TestCase):
    def test_primer_no_none_gana(self):
        """Devuelve el primer resultado no-None (mutante `is not`->`is`
        devolvía el primer None)."""
        from concurrent.futures import Future
        f1, f2 = Future(), Future()
        f1.set_result(None)
        f2.set_result({'meta': {'id': 'tt1'}})
        self.assertEqual(M._meta_race([f1, f2]), {'meta': {'id': 'tt1'}})

    def test_as_completed_timeout_45(self):
        """as_completed(futs, timeout=4.5): el budget de la carrera es
        4.5s, no otro (4.5+1@19)."""
        from concurrent.futures import Future
        f = Future()
        f.set_result(None)
        with patch.object(M, 'as_completed', return_value=[f]) as ac:
            M._meta_race([f])
        self.assertEqual(ac.call_args.kwargs.get('timeout'), 4.5)


class TestHandleMeta(unittest.TestCase):
    def test_cache_hit(self):
        key = ('movie', 'tt1')
        st._meta_c[key] = (__import__('time').time(), {'meta': {'id': 'tt1'}})
        self.assertEqual(M.handle_meta('movie', 'tt1'), {'meta': {'id': 'tt1'}})
        st._meta_c.clear()

    def test_prefetch_on_llama_collect(self):
        from tests import patch_fetch
        restore = patch_fetch(lambda u, t=8: None)  # hermetico: sin red real
        try:
            with patch.dict(M.__dict__, {'manifests': []}), \
                    patch.object(M, '_ensure_collect') as ec:
                M.handle_meta('movie', 'tt999')
                ec.assert_called_once_with(
                    ('movie', 'tt999', tuple(sorted(M._eff_disabled_for('movie')))))
        finally:
            restore()

    def test_prefetch_off_no_collect(self):
        """stream_prefetch=False: abrir la ficha NO lanza consultas a fuentes."""
        from tests import cfg_swap, patch_fetch
        _, undo = cfg_swap(stream_prefetch=False)
        restore = patch_fetch(lambda u, t=8: None)  # hermetico: sin red real
        try:
            with patch.dict(M.__dict__, {'manifests': []}), \
                    patch.object(M, '_ensure_collect') as ec:
                M.handle_meta('movie', 'tt999')
                ec.assert_not_called()
        finally:
            restore()
            undo()

    def test_prefetch_default_true(self):
        """Sin la clave stream_prefetch -> prefetch ON (mutante True@66:
        el default es True, no False)."""
        from tests import patch_fetch
        st.CFG.pop('stream_prefetch', None)
        restore = patch_fetch(lambda u, t=8: None)
        try:
            with patch.dict(M.__dict__, {'manifests': []}), \
                    patch.object(M, '_ensure_collect') as ec:
                M.handle_meta('movie', 'tt999')
                ec.assert_called_once()
        finally:
            restore()

    def test_fix_metas_deep_y_stale(self):
        """Ganador se fija con deep=True (True@73) y si todas las fuentes
        fallan cae al stale cacheado, no a {} (Or@76 + IsNot@72)."""
        import time
        from tests import patch_fetch
        key = ('movie', 'tt555')
        st._meta_c[key] = (time.time() - 601, {'meta': {'id': 'tt555'}})
        ok_calls = []
        def fake_fix(d, deep=False):
            ok_calls.append(deep)
        restore = patch_fetch(lambda u, t=8: None)   # todos los upstreams fallan
        try:
            src = type('S', (), {'base': 'http://src'})()
            with patch.dict(M.__dict__, {'manifests': [{}], 'SOURCES': [src]}), \
                    patch.object(M, '_ensure_collect'), \
                    patch.object(M, '_fix_metas', side_effect=fake_fix):
                out = M.handle_meta('movie', 'tt555')
        finally:
            restore()
            st._meta_c.pop(key, None)
        self.assertEqual(out, {'meta': {'id': 'tt555'}})   # stale, no {}

    def test_fix_metas_deep_true_en_exito(self):
        """La metadata ganadora se normaliza con deep=True (mutante True@73)."""
        from tests import patch_fetch
        calls = []
        restore = patch_fetch(lambda u, t=8: {'meta': {'id': 'tt777'}})
        try:
            src = type('S', (), {'base': 'http://src'})()
            with patch.dict(M.__dict__, {'manifests': [{}], 'SOURCES': [src]}), \
                    patch.object(M, '_ensure_collect'), \
                    patch.object(M, '_fix_metas',
                                 side_effect=lambda d, deep=False: calls.append(deep)):
                M.handle_meta('movie', 'tt777')
        finally:
            restore()
        self.assertEqual(calls, [True])

    def test_meta_tv_elige_canal_por_id(self):
        """_meta_tv usa el canal cuyo id == mid, no "el primero" (Eq@50)."""
        ch_a, ch_b = {'id': 'm3u:a'}, {'id': 'm3u:b'}
        with patch('conflux.domain.lists._m3u_metas',
                   return_value=[ch_a, ch_b]), \
                patch('conflux.infra.epg.epg_meta',
                      return_value={'id': 'm3u:b'}) as epg:
            out = M.handle_meta('tv', 'm3u:b')
        epg.assert_called_once_with(ch_b)
        self.assertEqual(out, {'meta': {'id': 'm3u:b'}})


if __name__ == '__main__':
    unittest.main()
