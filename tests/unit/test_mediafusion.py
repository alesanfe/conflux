# pylint: disable=missing-docstring,protected-access,unused-argument
"""Unit tests conflux.infra.mediafusion — provisionado lazy /encrypt-user-data."""
import importlib.util
import os
import unittest
from unittest.mock import MagicMock, patch

import conflux.core.state as st

# tests/__init__ pone un stub no-op en sys.modules['conflux.infra.mediafusion']
# (la provisión al importar es red real). Aquí cargamos el módulo REAL desde
# fichero — comparte estado global (SOURCES/manifests) por las importaciones.
_p = os.path.join(os.path.dirname(__file__), '..', '..',
                  'src', 'conflux', 'infra', 'mediafusion.py')
_spec = importlib.util.spec_from_file_location('conflux.infra.mediafusion_real',
                                               os.path.abspath(_p))
mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mf)


class TestProvision(unittest.TestCase):
    def setUp(self):
        self._cfg = dict(st.CFG)
        self._src = list(st.SOURCES)
        self._man = list(st.manifests)
        mf._once = False
        st.CFG['mediafusion'] = True

    def tearDown(self):
        st.CFG.clear(); st.CFG.update(self._cfg)
        st.SOURCES[:] = self._src
        st.manifests[:] = self._man
        mf._once = False

    def _run(self, secret='D-abc', man=None, enc_fail=None, fetch_fail=None):
        man = man if man is not None else {'catalogs': [{'id': 'tamil_hdrip'}]}
        enc = MagicMock(side_effect=enc_fail) if enc_fail else MagicMock(return_value=secret)
        ft = MagicMock(side_effect=fetch_fail) if fetch_fail else MagicMock(return_value=man)
        with patch.object(mf, '_encrypt', enc), patch.object(mf, '_fetch_manifest', ft):
            mf.provision()
        return enc, ft

    def test_ok_appends_source_and_manifest(self):
        self._run()
        mf_src = [s for s in st.SOURCES if s.name == 'MediaFusion']
        self.assertEqual(len(mf_src), 1)
        self.assertTrue(mf_src[0].base.endswith('/D-abc'))
        self.assertEqual(st.manifests[-1]['catalogs'][0]['id'], 'tamil_hdrip')
        self.assertTrue(mf._once)

    def test_idempotent(self):
        self._run()
        enc, ft = self._run()          # segunda llamada: no re-provisiona
        enc.assert_not_called()
        self.assertEqual(len([s for s in st.SOURCES if s.name == 'MediaFusion']), 1)

    def test_no_secret_no_source(self):
        self._run(secret='')
        self.assertFalse(any(s.name == 'MediaFusion' for s in st.SOURCES))
        self.assertFalse(mf._once)     # fallo -> reintenta en el próximo /reload

    def test_manifest_vacio_no_source(self):
        self._run(man={'catalogs': []})
        self.assertFalse(any(s.name == 'MediaFusion' for s in st.SOURCES))
        self.assertFalse(mf._once)

    def test_encrypt_falla_aislado(self):
        self._run(enc_fail=OSError('boom'))
        self.assertFalse(any(s.name == 'MediaFusion' for s in st.SOURCES))
        self.assertFalse(mf._once)

    def test_maybe_provision_gate(self):
        st.CFG['mediafusion'] = False
        with patch('threading.Thread') as th:
            mf.maybe_provision()
        th.assert_not_called()

    def test_maybe_provision_spawns_thread(self):
        with patch('threading.Thread') as th:
            mf.maybe_provision()
        th.assert_called_once()
        self.assertTrue(th.call_args.kwargs.get('daemon'))

    def test_no_duplica_con_source_previa(self):
        """SOURCES ya tiene MediaFusion y _once=False -> no añade otra
        (mutante Or@59: `_once or any()` -> and ignoraba el guarda)."""
        st.SOURCES.append(st.Source('MediaFusion', 'https://x/SEC'))
        self._run()
        self.assertEqual(
            len([s for s in st.SOURCES if s.name == 'MediaFusion']), 1)

    def test_encrypt_post_y_parse(self):
        """_encrypt real: POST a /encrypt-user-data, parsea encrypted_str
        (mutantes Add@40 y Or@44 viven dentro, el resto lo mockea)."""
        import json
        resp = MagicMock()
        resp.__enter__.return_value.read.side_effect = None
        resp.__enter__.return_value = MagicMock(
            read=MagicMock(return_value=json.dumps({'encrypted_str': 'S'}).encode()))
        with patch('urllib.request.urlopen', return_value=resp) as uo:
            self.assertEqual(mf._encrypt({'a': 1}), 'S')
        self.assertTrue(uo.call_args[0][0].full_url.endswith('/encrypt-user-data'))

    def test_fetch_manifest_url(self):
        """_fetch_manifest real pide base+'/manifest.json' (mutante Add@49:
        base-'/manifest.json' TypeErroraba dentro — y lo mockeaba todo)."""
        import conflux.infra.net as net
        with patch.object(net, 'fetch', return_value={'catalogs': []}) as f:
            self.assertEqual(mf._fetch_manifest('http://mf/SEC'),
                             {'catalogs': []})
        self.assertEqual(f.call_args[0][0], 'http://mf/SEC/manifest.json')

    def test_urlopen_timeout_15(self):
        """_encrypt -> urlopen(req, timeout=15) (mutante 15+1@43)."""
        import json
        resp = MagicMock()
        resp.__enter__.return_value = MagicMock(
            read=MagicMock(return_value=json.dumps({'encrypted_str': 'S'}).encode()))
        with patch('urllib.request.urlopen', return_value=resp) as uo:
            mf._encrypt({'a': 1})
        self.assertEqual(uo.call_args.kwargs.get('timeout'), 15)

    def test_fetch_manifest_timeout_15(self):
        """fetch(base+'/manifest.json', 15) — segundo arg posicional
        (mutante 15+1@49)."""
        import conflux.infra.net as net
        with patch.object(net, 'fetch', return_value={'catalogs': []}) as f:
            mf._fetch_manifest('http://mf/SEC')
        self.assertEqual(f.call_args[0][1], 15)

    def test_log_warning_trunca_100(self):
        """str(e)[:100] en el warning de provision (mutante 100+1@78)."""
        with patch.object(mf, '_log') as lg:
            self._run(enc_fail=OSError('x' * 150))
        self.assertEqual(len(lg.warning.call_args[0][1]), 100)

    def test_provision_con_otras_fuentes(self):
        """SOURCES con fuente no-MF no impide provisionar (mutante Eq@59:
        `s.name == 'MediaFusion'` -> `!=` en el primer guarda hace
        early-return siempre que haya cualquier fuente)."""
        st.SOURCES[:] = [st.Source('Otra', 'http://o')]
        self._run()
        self.assertTrue(any(s.name == 'MediaFusion' for s in st.SOURCES))

    def test_once_inicial_false(self):
        """_once arranca a False al cargar el módulo (mutante False@33:
        setUp lo resetea y lo enmascara — hay que observar un exec nuevo)."""
        spec = importlib.util.spec_from_file_location('mf_fresh',
                                                      os.path.abspath(_p))
        m2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m2)
        self.assertFalse(m2._once)

    def test_maybe_no_thread_si_ya_existe(self):
        """SOURCES == [MediaFusion] solo -> maybe_provision NO lanza hilo
        (mutante Eq@83: `s.name == 'MF'` -> `!=` — con otras fuentes en la
        lista el comportamiento coincide y el mutante es invisible)."""
        st.SOURCES[:] = [st.Source('MediaFusion', 'https://x/S')]
        with patch('threading.Thread') as th:
            mf.maybe_provision()
        th.assert_not_called()


if __name__ == '__main__':
    unittest.main()
