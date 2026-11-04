# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.easynews — solr-search -> /en/ b64."""
import base64
import time
import unittest
from unittest.mock import patch

import conflux.core.state as st
import conflux.infra.easynews as en
from tests import cfg_swap, fake_session


def _meta(title='Breaking Bad'):
    st._meta_c[('series', 'tt0944947')] = (time.time(), {'meta': {'name': title}})


class TestEnTitle(unittest.TestCase):
    def tearDown(self):
        st._meta_c.clear()

    def test_episodio_s_e(self):
        _meta()
        t, se = en._en_title('series', 'tt0944947:1:2')
        self.assertEqual((t, se), ('Breaking Bad', 's01e02'))

    def test_sin_meta_vacio(self):
        self.assertEqual(en._en_title('movie', 'tt999'), ('', None))

    def test_movie_sin_se(self):
        _meta('Fight Club')
        st._meta_c[('movie', 'tt0137523')] = (time.time(), {'meta': {'name': 'Fight Club'}})
        self.assertEqual(en._en_title('movie', 'tt0137523'), ('Fight Club', None))


class TestEnItem(unittest.TestCase):
    B = 'https://farm.example/dl'

    def test_dict_moderno_ok(self):
        s = en._en_item({'hash': 'h1', 'filename': 'f.mkv', 'ext': '.mkv',
                         'size': '1500000'}, self.B)
        self.assertEqual(s['name'], 'Easynews')
        self.assertTrue(s['url'].startswith('http://127.0.0.1:'))
        self.assertIn('/en/', s['url'])
        self.assertEqual(s['behaviorHints']['videoSize'], 1500000)
        link = base64.urlsafe_b64decode(s['url'].rsplit('/', 1)[-1] + '==').decode()
        self.assertIn('f.mkv', link)
        self.assertIn('h1', link)                    # hash real, no 'None' (Or@39)
        self.assertIn('h1.mkv', link)                # ext aplicada (Or@40)

    def test_array_legacy_hash_en_link(self):
        s = en._en_item({'0': 'h9', '10': 'leg.mkv', '11': '.mkv'}, self.B)
        link = base64.urlsafe_b64decode(s['url'].rsplit('/', 1)[-1] + '==').decode()
        self.assertIn('h9.mkv/leg.mkv.mkv', link)    # claves numéricas reales

    def test_array_legacy_ok(self):
        s = en._en_item({'0': 'h9', '10': 'leg.mkv', '11': '.mkv', '4': '200'}, self.B)
        self.assertIsNotNone(s)
        self.assertEqual(s['behaviorHints']['videoSize'], 200)

    def test_passwd_fuera(self):
        self.assertIsNone(en._en_item(
            {'hash': 'h', 'filename': 'x.mkv', 'passwd': True}, self.B))

    def test_virus_fuera(self):
        self.assertIsNone(en._en_item(
            {'hash': 'h', 'filename': 'x.mkv', 'virus': True}, self.B))

    def test_no_video_fuera(self):
        self.assertIsNone(en._en_item(
            {'hash': 'h', 'filename': 'x.mp3', 'type': 'AUDIO'}, self.B))

    def test_clip_corto_fuera(self):
        self.assertIsNone(en._en_item(
            {'hash': 'h', 'filename': 'x.mkv', 'duration': '90s'}, self.B))
        self.assertIsNone(en._en_item(
            {'hash': 'h', 'filename': 'x.mkv', 'duration': '3m'}, self.B))

    def test_sin_hash_o_nombre_none(self):
        self.assertIsNone(en._en_item({'filename': 'x.mkv'}, self.B))
        self.assertIsNone(en._en_item({'hash': 'h'}, self.B))

    def test_size_basura_ignorada(self):
        s = en._en_item({'hash': 'h', 'filename': 'x.mkv', 'size': 'abc'}, self.B)
        self.assertNotIn('videoSize', s['behaviorHints'])


class TestEasynews(unittest.TestCase):
    def tearDown(self):
        st._meta_c.clear()

    def test_sin_credenciales(self):
        _, undo = cfg_swap(easynews_user='', easynews_pass='')
        try:
            self.assertEqual(en._easynews('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_tipo_no_soportado(self):
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            self.assertEqual(en._easynews('tv', 'tt0137523'), [])
        finally:
            undo()

    def test_no_tt(self):
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            self.assertEqual(en._easynews('movie', 'kitsu:1'), [])
        finally:
            undo()

    def test_sin_titulo_no_pide(self):
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            with patch.object(en, '_session') as m:
                self.assertEqual(en._easynews('movie', 'tt999999'), [])
            m.assert_not_called()
        finally:
            undo()

    def test_happy_path(self):
        _meta()
        s, calls = fake_session({'json': {'downURL': 'https://f', 'dlFarm': 'a',
                                          'dlPort': 'b', 'data': [
                                              {'hash': 'h1', 'filename': 'bb.mkv',
                                               'ext': '.mkv', 'size': '999'}]}})
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            with patch.object(en, '_session', return_value=s):
                out = en._easynews('series', 'tt0944947:1:2')
        finally:
            undo()
        self.assertEqual(len(out), 1)
        self.assertEqual(calls[0][2]['timeout'], 10)   # mutante 10+1@80
        self.assertIn('/en/', out[0]['url'])
        self.assertGreaterEqual(st.HEALTH['Easynews']['ok'], 1)   # mutante True@86

    def test_error_red_vacio(self):
        _meta()
        s, _ = fake_session(Exception('down'))
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            with patch.object(en, '_session', return_value=s):
                self.assertEqual(en._easynews('series', 'tt0944947'), [])
        finally:
            undo()
        self.assertGreaterEqual(st.HEALTH['Easynews']['fail'], 1)  # mutante False@68

    def test_user_vacio_no_busca(self):
        """user='' (pw sí) -> [] sin pedir: el guarda corta por user (Or@58)."""
        _meta()
        s, _ = fake_session({'json': {'downURL': 'https://f', 'dlFarm': 'a',
                                      'dlPort': 'b', 'data': [
                                          {'hash': 'h1', 'filename': 'x.mkv'}]}})
        _, undo = cfg_swap(easynews_user='', easynews_pass='p')
        try:
            with patch.object(en, '_session', return_value=s) as m:
                self.assertEqual(en._easynews('series', 'tt0944947'), [])
            m.assert_not_called()
        finally:
            undo()

    def test_base_url_fallback_sin_downurl(self):
        """Sin downURL/dlFarm/dlPort -> base por defecto dl oficial.
        BUG: '/'.join de tres '' da '//' (truthy) -> el `or fallback` era
        código muerto y el link quedaba '//hash/file' (scheme-relative roto)."""
        _meta()
        s, _ = fake_session({'json': {'data': [{'hash': 'h1',
                                                'filename': 'x.mkv'}]}})
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            with patch.object(en, '_session', return_value=s):
                out = en._easynews('series', 'tt0944947')
        finally:
            undo()
        link = base64.urlsafe_b64decode(
            out[0]['url'].rsplit('/', 1)[-1] + '==').decode()
        self.assertTrue(link.startswith('https://members.easynews.com/dl/'),
                        link)

    def test_cap_40(self):
        _meta()
        s, _ = fake_session({'json': {'downURL': 'https://f', 'dlFarm': 'a',
                                      'dlPort': 'b', 'data': [
                                          {'hash': 'h%d' % i, 'filename': 'x%d' % i}
                                          for i in range(41)]}})
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            with patch.object(en, '_session', return_value=s):
                self.assertEqual(len(en._easynews('series', 'tt0944947')), 40)
        finally:
            undo()


if __name__ == '__main__':
    unittest.main()
