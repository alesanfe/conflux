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
