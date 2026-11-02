# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.wikidata — claims parsing."""
import unittest
from unittest.mock import patch

import conflux.infra.wikidata as W


class TestPv(unittest.TestCase):
    def test_primer_valor(self):
        claims = {'P17': [{'mainsnak': {'datavalue': {'value': 'es'}}}]}
        self.assertEqual(W._pv(claims, 'P17'), 'es')

    def test_sin_claim_none(self):
        self.assertIsNone(W._pv({}, 'P17'))


if __name__ == '__main__':
    unittest.main()




def _claim(v):
    return [{'mainsnak': {'datavalue': {'value': v}}}]


class TestWikidata(unittest.TestCase):
    def setUp(self):
        W._wd_c.clear()

    def tearDown(self):
        W._wd_c.clear()

    def test_full_flow_con_claims(self):
        claims = {
            'P856': _claim('https://web.com'),
            'P571': _claim({'time': '+2005-01-01T00:00:00Z'}),
            'P2002': _claim('@canal'),
        }
        s_payloads = {
            'w/api.php': {'json': {'query': {'search': [{'title': 'Q42'}]}}},
            'Special:EntityData': {'json': {'entities': {'Q42': {'claims': claims}}}},
        }
        seen = []
        def fake_fetch(url, t=6):
            seen.append(url)
            for k, spec in s_payloads.items():
                if k in url:
                    return spec['json']
            return None
        with patch.object(W, 'fetch', fake_fetch):
            out = W._wikidata('UCX')
        self.assertEqual(out['website'], 'https://web.com')
        self.assertEqual(out['year'], '2005')
        self.assertEqual(out['social'][0]['url'], 'https://x.com/canal')

    def test_cache_hit_sin_fetch(self):
        W._wd_c['UCX'] = (W.time.time(), {'country': 'JP'})
        with patch.object(W, 'fetch', side_effect=AssertionError('no fetch')):
            self.assertEqual(W._wikidata('UCX'), {'country': 'JP'})

    def test_upstream_sin_search_vacio(self):
        with patch.object(W, 'fetch', return_value={'query': {'search': []}}):
            self.assertEqual(W._wikidata('UCY'), {})


class TestWdCountry(unittest.TestCase):
    def test_resuelve_segunda_entidad(self):
        claims = {'P17': _claim({'id': 'Q30'})}
        with patch.object(W, 'fetch', return_value={
                'entities': {'Q30': {'labels': {'en': {'value': 'United States'}}}}}):
            self.assertEqual(W._wd_country(claims), 'United States')

    def test_sin_pais_none(self):
        self.assertIsNone(W._wd_country({}))


class TestWdClaimsOut(unittest.TestCase):
    def test_completo(self):
        c = {'P856': _claim('https://w'), 'P571': _claim({'time': '+2010-01-01'}),
             'P2003': _claim('insta_handle')}
        with patch.object(W, '_wd_country', return_value='ES'):
            out = W._wd_claims_out(c)
        self.assertEqual(out['country'], 'ES')
        self.assertEqual(out['website'], 'https://w')
        self.assertEqual(out['year'], '2010')
        self.assertIn('instagram.com/insta_handle', out['social'][0]['url'])

    def test_vacio(self):
        self.assertEqual(W._wd_claims_out({}), {})
