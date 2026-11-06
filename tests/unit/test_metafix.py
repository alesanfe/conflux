# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position,too-few-public-methods
"""Unit tests conflux.domain.metafix — fix de metas."""
import unittest
from unittest.mock import MagicMock, patch

import conflux.domain.metafix as mf
from tests import fake_session


class TestFixMetas(unittest.TestCase):
    def test_yt_id_desde_id(self):
        d = {'meta': {'id': 'yt_id:UCabc'}}
        with patch.object(mf, '_fix_meta_basic') as b, \
             patch.object(mf, '_fix_meta_deep'):
            mf._fix_metas(d)
            b.assert_called_once()

    def test_sin_yt_salta(self):
        d = {'meta': {'id': 'tt123'}}
        with patch.object(mf, '_fix_meta_basic') as b:
            mf._fix_metas(d)
            b.assert_not_called()

    def test_metas_lista(self):
        d = {'metas': [{'id': 'yt_id:A'}, {'id': 'yt_id:B'}]}
        with patch.object(mf, '_fix_meta_basic') as b:
            mf._fix_metas(d)
            self.assertEqual(b.call_count, 2)


if __name__ == '__main__':
    unittest.main()


class TestFmtNum(unittest.TestCase):
    def test_rangos(self):
        self.assertEqual(mf._fmt_num(2e9), '2.0B')
        self.assertEqual(mf._fmt_num(3e6), '3.0M')
        self.assertEqual(mf._fmt_num(4e3), '4.0K')
        self.assertEqual(mf._fmt_num(500), '500')
        self.assertIsNone(mf._fmt_num('x'))
        self.assertIsNone(mf._fmt_num(None))

    def test_bordes_exactos(self):
        """n>=d en el umbral: 1e9->B, 1e6->M, 1e3->K, 999->int (GtE@25, +1s)."""
        self.assertEqual(mf._fmt_num(1e9), '1.0B')
        self.assertEqual(mf._fmt_num(1e9 - 1), '1000.0M')
        self.assertEqual(mf._fmt_num(1e6), '1.0M')
        self.assertEqual(mf._fmt_num(1e6 - 1), '1000.0K')
        self.assertEqual(mf._fmt_num(1e3), '1.0K')
        self.assertEqual(mf._fmt_num(999), '999')


class TestFixMetaDescBorde(unittest.TestCase):
    def test_genre_cap_3(self):
        """g[:3] en la descripción (3+1@41: 4º género no entra)."""
        m = {'genre': ['a', 'b', 'c', 'cuarto']}
        mf._fix_meta_desc(m)
        self.assertIn('a · b · c', m['description'])
        self.assertNotIn('cuarto', m['description'])


class TestFixMetaPosterDominio(unittest.TestCase):
    def test_googleusercontent_tambien(self):
        """googleusercontent caduca igual que ggpht (In@49: notin no tocaría)."""
        m = {'poster': 'https://lh3.googleusercontent.com/x'}
        mf._fix_meta_poster(m, 'UCY')
        self.assertEqual(m['poster'], 'https://unavatar.io/youtube/UCY')
        m2 = {'poster': 'https://cdn.otro.com/ok'}
        mf._fix_meta_poster(m2, 'UCY')
        self.assertEqual(m2['poster'], 'https://cdn.otro.com/ok')


class TestAwardsBordes(unittest.TestCase):
    def test_umbrales_exactos(self):
        """subs_n >= t en cada escalón (umbrales +1, GtE@80)."""
        for subs, tag in [(1e8, 'Red Diamond'), (5e7, 'Ruby'),
                          (1e7, 'Diamond'), (1e6, 'Gold'), (1e5, 'Silver')]:
            m = {'popularity': str(int(subs))}
            mf._fix_meta_awards(m)
            self.assertIn(tag, m.get('awards', ''), f'{subs} -> {tag}')
        m = {'popularity': '99999'}
        mf._fix_meta_awards(m)
        self.assertNotIn('awards', m)                 # justo bajo Silver


class TestFixMetaVidsDead(unittest.TestCase):
    def test_solo_muertos_fuera(self):
        """notin dead conserva (NotIn@109: in dejaría SOLO los muertos)."""
        m = {'videos': [{'id': 'v:A'}, {'id': 'v:B'}]}
        mf._fix_meta_vids(m, m['videos'], {'v:A'})
        self.assertEqual([v['id'] for v in m['videos']], ['v:B'])


class TestRpdb(unittest.TestCase):
    """_rpdb: posters RatingPosterDB solo con key y metas tt:*."""

    def test_con_key_sustituye_poster_tt(self):
        """(not-elim@130: quitar 'not key' desactivaría rpdb)."""
        _, undo = __import__('tests').cfg_swap(rpdb_key='k9')
        try:
            d = {'meta': {'id': 'tt123', 'poster': 'p'},
                 'metas': [{'id': 'tt1'}, {'id': 'kitsu:9'}]}
            out = mf._rpdb(d)
        finally:
            undo()
        self.assertIn('api.ratingposterdb.com/k9/imdb/poster-default/tt123',
                      out['meta']['poster'])
        self.assertIn('poster-default/tt1', out['metas'][0]['poster'])
        self.assertNotIn('poster', out['metas'][1])     # no-tt intacto

    def test_no_dict_devuelve_tal_cual(self):
        """isinstance(d, dict) guard (Or@130/and: dict('x') crashea)."""
        _, undo = __import__('tests').cfg_swap(rpdb_key='k9')
        try:
            self.assertEqual(mf._rpdb('no-dict'), 'no-dict')
        finally:
            undo()

    def test_meta_sin_id_no_sustituye(self):
        """meta dict sin id -> startswith('tt') False -> intacto
        (Or@137/140: or -> poster .../None.jpg)."""
        _, undo = __import__('tests').cfg_swap(rpdb_key='k9')
        try:
            out = mf._rpdb({'metas': [{'name': 'x'}],
                            'meta': {'name': 'y'}})
        finally:
            undo()
        self.assertNotIn('poster', out['metas'][0])
        self.assertNotIn('poster', out['meta'])

    def test_sin_key_no_toca(self):
        _, undo = __import__('tests').cfg_swap(rpdb_key='')
        try:
            d = {'meta': {'id': 'tt1', 'poster': 'p'}}
            self.assertIs(mf._rpdb(d), d)
        finally:
            undo()


class TestFixMetasDeepGate(unittest.TestCase):
    def test_sin_deep_no_llama_deep(self):
        """deep=False default (False@146: True llamaría wikidata en listado)."""
        d = {'meta': {'id': 'yt_id:UCX', 'videos': [{'id': 'v:A'}]}}
        with patch.object(mf, '_wikidata') as wd, \
                patch.object(mf, '_dead_videos', return_value=set()):
            mf._fix_metas(d)
        wd.assert_not_called()

    def test_yt_presente_no_pisado_por_id(self):
        """yt ya set + id 'yt_id:otro' -> yt conserva (And@157: or lo pisa)."""
        d = {'meta': {'id': 'yt_id:ZZZ', 'yt_id': 'REAL'}}
        seen = []
        with patch.object(mf, '_fix_one_meta',
                          lambda m, yt, deep: seen.append(yt)):
            mf._fix_metas(d)
        self.assertEqual(seen, ['REAL'])


class TestFixMetaFields(unittest.TestCase):
    def test_poster_ggpht_a_unavatar(self):
        m = {'poster': 'https://ggpht.com/x', 'name': 'Canal'}
        mf._fix_meta_poster(m, 'UCX')
        self.assertEqual(m['poster'], 'https://unavatar.io/youtube/UCX')
        self.assertEqual(m['logo'], m['poster'])

    def test_desc_stats_reales(self):
        m = {'popularity': '2000000', 'viewCount': '50000', 'genre': ['a', 'b']}
        mf._fix_meta_desc(m)
        self.assertIn('2.0M suscriptores', m['description'])
        self.assertIn('50.0K reproducciones', m['description'])
        self.assertIn('a · b', m['description'])

    def test_fields_keywords_fallback(self):
        m = {'name': 'Canal', 'poster': 'x'}
        mf._fix_meta_fields(m, 'UCX')
        self.assertEqual(m['keywords'], 'Canal')
        self.assertIn('Canal de YouTube', m['description'])


class TestFixMetaAwards(unittest.TestCase):
    def test_award_por_subs(self):
        m = {'popularity': '15000000'}
        mf._fix_meta_awards(m)
        self.assertIn('YouTube', m['awards'])

    def test_sin_subs_sin_award(self):
        m = {}
        mf._fix_meta_awards(m)
        self.assertNotIn('awards', m)

    def test_award_existente_no_pisa(self):
        m = {'popularity': '15000000', 'awards': 'Emmy'}
        mf._fix_meta_awards(m)
        self.assertEqual(m['awards'], 'Emmy')


class TestFixMetaWd(unittest.TestCase):
    def test_rellena_vacios_solo(self):
        m = {'country': 'ES'}
        mf._fix_meta_wd(m, {'country': 'US', 'year': '2005',
                            'website': 'https://w', 'social': [
                                {'name': 'X', 'category': 'social', 'url': 'u'}]})
        self.assertEqual(m['country'], 'ES', 'no pisa lo existente')
        self.assertEqual(m['releaseInfo'], '2005')
        self.assertEqual(len(m['links']), 2)


class TestFixMetaVids(unittest.TestCase):
    def test_backdrop_trailer_y_poda(self):
        m = {'videos': [{'id': 'v:AAA'}, {'id': 'v:BBB'}]}
        mf._fix_meta_vids(m, m['videos'], {'v:BBB'})
        self.assertTrue(m['background'].endswith('/AAA/hqdefault.jpg'))
        self.assertEqual(m['unsubscribedTrailer'], 'AAA')
        self.assertEqual(len(m['videos']), 1)


class TestFixMetaDeep(unittest.TestCase):
    def test_deep_llama_wd_y_dead(self):
        m = {'videos': [{'id': 'v:A'}]}
        with patch.object(mf, '_wikidata', return_value={'country': 'JP'}), \
             patch.object(mf, '_dead_videos', return_value=set()):
            mf._fix_meta_deep(m, 'UC')
        self.assertEqual(m['country'], 'JP')
        self.assertIn('background', m)


class TestDeadVideos(unittest.TestCase):
    def test_marca_404(self):
        class _R:
            def __init__(self, code):
                self.status_code = code
        s = MagicMock()
        s.head.side_effect = lambda url, **kw: _R(404 if 'A' in url else 200)
        vids = [{'id': 'v:A'}, {'id': 'v:B'}]
        with patch.object(mf, '_session', return_value=s):
            dead = mf._dead_videos(vids)
        self.assertEqual(dead, {'v:A'})
        self.assertEqual(s.head.call_args.kwargs['timeout'], 4)   # mutante 4+1@180

    def test_cap_40(self):
        """vids[:40] — 41 candidatos -> solo 40 HEAD (mutante 40+1@184)."""
        class _R:
            status_code = 200
        s = MagicMock()
        s.head.side_effect = lambda url, **kw: _R()
        vids = [{'id': f'v:{i}'} for i in range(41)]
        with patch.object(mf, '_session', return_value=s):
            mf._dead_videos(vids)
        self.assertEqual(s.head.call_count, 40)

    def test_error_red_no_mata(self):
        import requests
        s, _ = fake_session(requests.RequestException('x'))
        with patch.object(mf, '_session', return_value=s):
            self.assertEqual(mf._dead_videos([{'id': 'v:A'}]), set())


class TestFixMetasDeep(unittest.TestCase):
    def test_deep_integra(self):
        d = {'meta': {'id': 'yt_id:UCX', 'videos': [{'id': 'v:A'}]}}
        with patch.object(mf, '_wikidata', return_value={}), \
             patch.object(mf, '_dead_videos', return_value=set()):
            out = mf._fix_metas(d, deep=True)
        self.assertIs(out, d)
        self.assertIn('background', d['meta'])


class TestFixMetaHtml(unittest.TestCase):
    """stremio-web renderiza description como texto: el HTML de AniList
    salía literal (<i>...</i> visible en la ficha — hallazgo visual)."""

    def test_strip_tags_y_entidades(self):
        m = {'description': 'x <i>Título</i><br>Pepe &amp; Ana&nbsp;<b>y</b>'}
        mf._fix_meta_html(m)
        self.assertEqual(m['description'], 'x Título Pepe & Ana y')

    def test_sin_html_no_toca(self):
        m = {'description': '3 < 4 y texto plano'}
        mf._fix_meta_html(m)
        self.assertEqual(m['description'], '3 < 4 y texto plano')

    def test_sin_descripcion_no_rompe(self):
        mf._fix_meta_html({})
        mf._fix_meta_html({'description': None})
        mf._fix_meta_html({'description': 42})

    def test_campos_multilinea_a_inline(self):
        # IPTV upstream: 'Country: CR\nCategories: x' — el cliente colapsa
        # el \n y queda 'CR Categories' corrido -> unir con ' · '
        m = {'description': 'Country: CR\nCategories: entertainment'}
        mf._fix_meta_html(m)
        self.assertEqual(m['description'], 'Country: CR · Categories: entertainment')

    def test_prosa_multilinea_no_es_campo(self):
        m = {'description': 'Primera frase normal\nsegunda línea de prosa.'}
        mf._fix_meta_html(m)
        self.assertEqual(m['description'], 'Primera frase normal\nsegunda línea de prosa.')


class TestPosterDead(unittest.TestCase):
    """Poster 404 de upstream (metahub sin cobertura) -> placeholder en la
    vista; el desconocido se HEAD-verifica async en vez de bloquear."""

    def setUp(self):
        self._b, self._o, self._p = (dict(mf._POSTER_BAD), dict(mf._POSTER_OK),
                                   set(mf._POSTER_PEND))
        mf._POSTER_BAD.clear(); mf._POSTER_OK.clear(); mf._POSTER_PEND.clear()

    def tearDown(self):
        mf._POSTER_BAD.clear(); mf._POSTER_BAD.update(self._b)
        mf._POSTER_OK.clear(); mf._POSTER_OK.update(self._o)
        mf._POSTER_PEND.clear(); mf._POSTER_PEND.update(self._p)

    def test_vista_sustituye_sin_mutar_cache(self):
        m = {'poster': 'http://x/dead.jpg', 'name': 'x'}
        mf._POSTER_BAD['http://x/dead.jpg'] = (2, mf._CLOCK.time())
        d = {'metas': [m]}
        out = mf._posters_view(d)
        self.assertEqual(out['metas'][0]['poster'], mf._POSTER_FB)
        self.assertEqual(m['poster'], 'http://x/dead.jpg')   # original intacto

    def test_vista_meta_singular(self):
        mf._POSTER_BAD['http://x/dead.jpg'] = (2, mf._CLOCK.time())
        out = mf._posters_view({'meta': {'poster': 'http://x/dead.jpg'}})
        self.assertEqual(out['meta']['poster'], mf._POSTER_FB)

    def test_vista_metas_detailed(self):
        mf._POSTER_BAD['http://x/d.jpg'] = (2, mf._CLOCK.time())
        out = mf._posters_view({'metasDetailed': [{'poster': 'http://x/d.jpg'},
                                                  {'poster': 'http://x/ok.jpg'}]})
        self.assertEqual(out['metasDetailed'][0]['poster'], mf._POSTER_FB)
        self.assertEqual(out['metasDetailed'][1]['poster'], 'http://x/ok.jpg')

    def test_sin_muertos_devuelve_el_mismo_objeto(self):
        d = {'metas': [{'poster': 'http://x/ok.jpg'}]}
        mf._POSTER_OK['http://x/ok.jpg'] = mf._CLOCK.time()
        self.assertIs(mf._posters_view(d), d)

    def test_strike_simple_no_sustituye(self):
        mf._POSTER_BAD['http://x/flaky.jpg'] = (1, mf._CLOCK.time())
        out = mf._posters_view({'metas': [{'poster': 'http://x/flaky.jpg'}]})
        self.assertEqual(out['metas'][0]['poster'], 'http://x/flaky.jpg')

    def test_veredicto_viejo_reverifica_no_sustituye(self):
        mf._POSTER_BAD['http://x/old.jpg'] = (2, mf._CLOCK.time() - 1801)
        with patch.object(mf, '_POOL_BG') as bg:
            out = mf._posters_view({'metas': [{'poster': 'http://x/old.jpg'}]})
            bg.submit.assert_called_once()
        self.assertEqual(out['metas'][0]['poster'], 'http://x/old.jpg')

    def test_desconocido_encola_head(self):
        with patch.object(mf, '_POOL_BG') as bg:
            mf._poster_probe({'poster': 'http://x/new.jpg'})
            bg.submit.assert_called_once()
        self.assertIn('http://x/new.jpg', mf._POSTER_PEND)

    def test_ok_reciente_no_reverifica(self):
        mf._POSTER_OK['http://x/fine.jpg'] = mf._CLOCK.time()
        with patch.object(mf, '_POOL_BG') as bg:
            mf._poster_probe({'poster': 'http://x/fine.jpg'})
            bg.submit.assert_not_called()

    def test_head_404_marca_muerto(self):
        s, _ = fake_session({'status': 404})
        with patch.object(mf, '_session', return_value=s):
            mf._poster_head('http://x/dead.jpg')
        self.assertEqual(mf._POSTER_BAD['http://x/dead.jpg'][0], 2)

    def test_head_200_marca_ok(self):
        s, _ = fake_session({'status': 200})
        with patch.object(mf, '_session', return_value=s):
            mf._poster_head('http://x/live.jpg')
        self.assertIn('http://x/live.jpg', mf._POSTER_OK)

    def test_head_timeout_es_strike(self):
        s, _ = fake_session(Exception('boom'))
        with patch.object(mf, '_session', return_value=s):
            mf._poster_head('http://x/t.jpg')
        self.assertEqual(mf._POSTER_BAD['http://x/t.jpg'][0], 1)

    def test_sin_poster_no_rompe(self):
        mf._poster_probe({})
        mf._poster_probe({'poster': None})
        mf._poster_probe({'poster': 'relative/path'})
        mf._poster_probe({'poster': 'data:image/png;base64,x'})

    def test_vista_items_no_dict_no_rompe(self):
        d = {'metas': ['no-dict', 42, {'poster': 'http://x/ok.jpg'}]}
        with patch.object(mf, '_POOL_BG'):
            self.assertIs(mf._posters_view(d), d)

    def test_fix_metas_limpia_desc_de_cualquier_meta(self):
        d = {'metas': [{'id': 'tt1', 'description': '<i>a</i>'},
                       {'id': 'anilist:2', 'description': '<b>b</b>'}]}
        mf._fix_metas(d)
        self.assertEqual([x['description'] for x in d['metas']], ['a', 'b'])
