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
            seen.append((url, t))
            for k, spec in s_payloads.items():
                if k in url:
                    return spec['json']
            return None
        with patch.object(W, 'fetch', fake_fetch):
            out = W._wikidata('UCX')
        self.assertEqual(out['website'], 'https://web.com')
        self.assertEqual(out['year'], '2005')
        self.assertEqual(out['social'][0]['url'], 'https://x.com/canal')
        # timeouts: search 6s, entity 8s (6+1@35 / 8+1@38)
        self.assertEqual(seen[0][1], 6)
        self.assertEqual(seen[1][1], 8)

    def test_wd_country_timeout_6(self):
        """La segunda EntityData va con timeout=6 (6+1@57)."""
        claims = {'P17': _claim({'id': 'Q30'})}
        seen = []
        def fake_fetch(url, t=0):
            seen.append(t)
            return {'entities': {'Q30': {'labels': {'en': {'value': 'US'}}}}}
        with patch.object(W, 'fetch', fake_fetch):
            W._wd_country(claims)
        self.assertEqual(seen, [6])

    def test_cache_hit_sin_fetch(self):
        W._wd_c['UCX'] = (W.time.time(), {'country': 'JP'})
        with patch.object(W, 'fetch', side_effect=AssertionError('no fetch')):
            self.assertEqual(W._wikidata('UCX'), {'country': 'JP'})

    def test_upstream_sin_search_vacio(self):
        with patch.object(W, 'fetch', return_value={'query': {'search': []}}):
            self.assertEqual(W._wikidata('UCY'), {})

    def test_purga_sobre_400(self):
        """401 entradas -> purga las 200 más viejas antes de añadir (400+1, 200+1)."""
        import time
        W._wd_c.update({f'ch{i}': (time.time() - i, {}) for i in range(401)})
        with patch.object(W, 'fetch', return_value=None):
            W._wikidata('nuevo')
        self.assertEqual(len(W._wd_c), 202)          # 401-200 purgados +1
        self.assertIn('nuevo', W._wd_c)

    def test_no_purga_en_400(self):
        """Exactamente 400 -> NO purga: la cota es >400 no >=400 (Gt@27)."""
        import time
        W._wd_c.update({f'ch{i}': (time.time() - i, {}) for i in range(400)})
        with patch.object(W, 'fetch', return_value=None):
            W._wikidata('nuevo')
        self.assertEqual(len(W._wd_c), 401)          # 400+1, sin purga

    def test_ttl_24h_exacto_refresca(self):
        """edad == 86400s -> caducado: < no <= (mutante Lt@31, freezegun)."""
        import time
        from freezegun import freeze_time
        with freeze_time('2025-01-01 12:00:00'):
            W._wd_c['UCX'] = (time.time() - 86400, {'country': 'JP'})
            with patch.object(W, 'fetch', return_value=None) as f:
                W._wikidata('UCX')
            f.assert_called_once()                   # caducado -> refetch


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
