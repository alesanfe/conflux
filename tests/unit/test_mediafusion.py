# pylint: disable=missing-docstring,protected-access,unused-argument
"""Unit tests conflux.infra.mediafusion — provisionado lazy /encrypt-user-data."""
import unittest
from unittest.mock import MagicMock, patch

import conflux.core.state as st
import conflux.infra.mediafusion as mf


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


if __name__ == '__main__':
    unittest.main()
