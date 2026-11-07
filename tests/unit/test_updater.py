# pylint: disable=missing-docstring,protected-access
"""Unit tests conflux.infra.updater — versionado, canal, verificación, staged."""
import hashlib
import json
import unittest
from unittest.mock import patch

import conflux.infra.updater as U


def _rel(tag='v2.0.0', pre=False, assets=None):
    e = U._EXE                                    # conflux.exe | -linux | -macos
    return {'tag': tag, 'pre': pre,
            'assets': assets or {
                e: 'https://x/exe',
                f'{e}.sha256': 'https://x/sha',
                f'{e}.sha256.sigstore.json': 'https://x/sig'}}


class TestVerKey(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(U._ver_key('v1.2.3'), (1, 2, 3, ''))
        self.assertEqual(U._ver_key('1.2.3-beta.4'), (1, 2, 3, 'beta.4'))
        self.assertIsNone(U._ver_key(''))
        self.assertIsNone(U._ver_key('nope'))
        self.assertIsNone(U._ver_key(None))

    def test_cmp(self):
        c = U._ver_cmp
        self.assertEqual(c('v1.0.0', '1.0.0'), 0)
        self.assertEqual(c('v2.0.0', 'v1.9.9'), 1)
        self.assertEqual(c('v1.0.0-beta', 'v1.0.0'), -1)   # pre < release
        self.assertEqual(c('v1.0.0-beta.2', 'v1.0.0-beta.1'), 1)
        self.assertIsNone(c('bad', 'v1.0.0'))               # raro -> None

    def test_cmp_prerelease_numerico(self):
        """Semver: beta.10 > beta.9 — léxico puro lo ordena al revés."""
        c = U._ver_cmp
        self.assertEqual(c('v1.0.0-beta.10', 'v1.0.0-beta.9'), 1)
        self.assertEqual(c('v1.0.0-beta.9', 'v1.0.0-beta.10'), -1)
        self.assertEqual(c('v1.0.0-beta.2.1', 'v1.0.0-beta.2'), 1)  # más segs >


class TestLatest(unittest.TestCase):
    def _rels(self, items):
        return json.dumps([{'tag_name': t, 'prerelease': p, 'draft': False,
                            'assets': [{'name': n, 'browser_download_url': u}
                                       for n, u in a.items()]}
                           for t, p, a in items]).encode()

    def test_stable_salta_prereleases(self):
        rels = self._rels([('v2.0.0-beta.1', True, {}),
                           ('v1.5.0', False, {'a': 'u'})])
        with patch.object(U, '_dl', return_value=rels):
            r = U._latest('stable')
        self.assertEqual(r['tag'], 'v1.5.0')

    def test_beta_acepta_prereleases(self):
        rels = self._rels([('v2.0.0-beta.1', True, {}), ('v1.5.0', False, {})])
        with patch.object(U, '_dl', return_value=rels):
            r = U._latest('beta')
        self.assertEqual(r['tag'], 'v2.0.0-beta.1')

    def test_prerelease_solo_por_tag(self):
        # '-' en el tag cuenta como pre aunque el flag diga False
        rels = self._rels([('v2.0.0-rc.1', False, {}), ('v1.5.0', False, {})])
        with patch.object(U, '_dl', return_value=rels):
            self.assertEqual(U._latest('stable')['tag'], 'v1.5.0')

    def test_draft_y_json_roto(self):
        rels = self._rels([('v9.9.9', False, {})])
        rels = rels.replace(b'"draft": false', b'"draft": true')
        with patch.object(U, '_dl', return_value=rels):
            self.assertIsNone(U._latest('stable'))
        with patch.object(U, '_dl', return_value=b'{no json'):
            self.assertIsNone(U._latest('stable'))
        with patch.object(U, '_dl', return_value=None):
            self.assertIsNone(U._latest('stable'))


class TestVerifyHash(unittest.TestCase):
    def test_ok_y_formato(self):
        exe = b'EXE_BYTES'
        sha = hashlib.sha256(exe).hexdigest()
        self.assertTrue(U._verify_hash(exe, f'{sha}  conflux.exe'.encode()))
        self.assertTrue(U._verify_hash(exe, sha.encode()))          # hash suelto
        self.assertTrue(U._verify_hash(exe, sha.upper().encode()))  # case-insensitive

    def test_mismatch(self):
        exe = b'EXE'
        self.assertFalse(U._verify_hash(exe, b'0' * 64 + b'  otro'))
        self.assertFalse(U._verify_hash(b'OTRO', hashlib.sha256(exe).hexdigest().encode()))
        self.assertFalse(U._verify_hash(exe, b''))
        self.assertFalse(U._verify_hash(exe, b'no-hex'))
        self.assertFalse(U._verify_hash(exe, None))


class TestSigVerify(unittest.TestCase):
    def test_unavailable(self):
        self.assertEqual(U._sig_verify(b'x', None), 'unavailable')

    def test_sin_lib_unavailable(self):
        """Sin la lib sigstore -> 'unavailable' determinista. Si está instalada,
        Verifier.production() refresca la raíz TUF de sigstore.dev — red real
        (~12s): aquí se fuerza el path ImportError, la lib NO puede tocarla."""
        import sys
        import unittest.mock as um
        block = {k: None for k in list(sys.modules)
                 if k == 'sigstore' or k.startswith('sigstore.')}
        block.update({'sigstore': None, 'sigstore.models': None,
                      'sigstore.verify': None})
        with um.patch.dict(sys.modules, block):
            self.assertEqual(U._sig_verify(b'x', b'{}'), 'unavailable')

    def test_firma_mala_failed(self):
        """Bundle presente + lib stubbeada: verify_artifact lanza -> 'failed'
        (un bundle inválido es RECHAZO, no 'unavailable' que instala igual)."""
        import sys
        import unittest.mock as um

        class _B:
            @staticmethod
            def from_json(_b):
                return object()

        class _V:
            @staticmethod
            def production():
                raise RuntimeError('tuf')

        fake_verify = um.MagicMock()
        fake_verify.policy = um.MagicMock()
        fake_verify.Verifier = _V
        fake_models = um.MagicMock()
        fake_models.Bundle = _B
        with um.patch.dict(sys.modules,
                           {'sigstore': um.MagicMock(),
                            'sigstore.models': fake_models,
                            'sigstore.verify': fake_verify}):
            self.assertEqual(U._sig_verify(b'x', b'{}'), 'failed')


class TestDl(unittest.TestCase):
    def test_url_ausente_rechaza(self):
        """Asset con browser_download_url None/'' -> None, no excepción."""
        self.assertIsNone(U._dl(None))
        self.assertIsNone(U._dl(''))

    def test_url_no_publica_rechaza(self):
        with patch.object(U, '_public_url', return_value=False):
            self.assertIsNone(U._dl('http://169.254.169.254/x'))


class TestUpdateTick(unittest.TestCase):
    def setUp(self):
        U._UPD['last'] = 0.0

    def test_optin_y_frozen(self):
        with patch.object(U, '_cfgget', return_value=False):
            self.assertEqual(U._update_tick(), 0)                  # auto_update off
        with patch.object(U.sys, 'frozen', False, create=True):
            with patch.object(U, '_cfgget', return_value=True):
                self.assertEqual(U._update_tick(), 0)              # no frozen -> no-op

    def test_tick_cadencia(self):
        U._UPD['last'] = U.CLOCK.time()                            # recién chequeado
        with patch.object(U, '_cfgget', return_value=True), \
                patch.object(U.sys, 'frozen', True, create=True):
            self.assertEqual(U._update_tick(), 0)                  # <24h -> skip
        U._UPD['last'] = 0.0

    def _frozen_tick(self, rel, dl_map):
        """tick con frozen+opt-in; dl_map: url->bytes o callable."""
        def dl(url, **_kw):
            v = dl_map.get(url) if isinstance(dl_map, dict) else dl_map(url)
            return v
        st = patch.object(U, '_stage', return_value='/tmp/cmd')
        patches = [
            patch.object(U, '_cfgget', return_value=True),
            patch.object(U.sys, 'frozen', True, create=True),
            patch.object(U, '_latest', return_value=rel),
            patch.object(U, '_dl', side_effect=dl),
            patch.object(U, '_sig_verify', return_value='unavailable'),
        ]
        mocks = [p.start() for p in patches] + [st.start()]
        try:
            return U._update_tick(), mocks[-1]
        finally:
            st.stop()
            for p in patches:
                p.stop()

    def test_ya_al_dia(self):
        with patch.object(U, '_version', return_value='2.0.0'):
            self.assertEqual(self._frozen_tick(_rel('v2.0.0'), {})[0], 0)
        with patch.object(U, '_version', return_value='3.0.0'):
            self.assertEqual(self._frozen_tick(_rel('v2.0.0'), {})[0], 0)

    def test_stages_con_update(self):
        exe = b'NEW_EXE'
        sha = hashlib.sha256(exe).hexdigest() + '  conflux.exe'
        dl = {'https://x/exe': exe, 'https://x/sha': sha.encode(),
              'https://x/sig': b'{}'}
        with patch.object(U, '_version', return_value='1.0.0'):
            r, st = self._frozen_tick(_rel('v2.0.0'), dl)
        self.assertEqual(r, 1)
        st.assert_called_once()

    def test_hash_mismatch_rechaza(self):
        dl = {'https://x/exe': b'EXE', 'https://x/sha': b'0' * 64,
              'https://x/sig': b'{}'}
        with patch.object(U, '_version', return_value='1.0.0'):
            r, st = self._frozen_tick(_rel('v2.0.0'), dl)
        self.assertEqual(r, 0)
        st.assert_not_called()

    def test_assets_incompletos(self):
        rel = _rel('v2.0.0', assets={U._EXE: 'https://x/exe'})
        with patch.object(U, '_version', return_value='1.0.0'):
            r, st = self._frozen_tick(rel, {})
        self.assertEqual(r, 0)
        st.assert_not_called()

    def test_release_no_encontrada(self):
        with patch.object(U, '_version', return_value='1.0.0'):
            self.assertEqual(self._frozen_tick(None, {})[0], 0)

    def test_sigstore_verifica_el_checksum(self):
        """El bundle firma conflux.exe.sha256 (cosign sign-blob en el
        workflow), NO el exe — _sig_verify debe recibir los bytes del
        checksum. Verificar el binario haría fallar siempre la firma."""
        exe = b'NEW_EXE'
        sha_bytes = (hashlib.sha256(exe).hexdigest() + '  conflux.exe').encode()
        dl = {'https://x/exe': exe, 'https://x/sha': sha_bytes, 'https://x/sig': b'BUNDLE'}
        with patch.object(U, '_cfgget', return_value=True), \
                patch.object(U.sys, 'frozen', True, create=True), \
                patch.object(U, '_latest', return_value=_rel('v2.0.0')), \
                patch.object(U, '_dl', side_effect=lambda u, **_k: dl.get(u)), \
                patch.object(U, '_stage', return_value='/tmp/x'), \
                patch.object(U, '_version', return_value='1.0.0'), \
                patch.object(U, '_sig_verify', return_value='verified') as sv:
            U._update_tick()
        sv.assert_called_once_with(sha_bytes, b'BUNDLE')

    def test_stage_oserror_fail_safe(self):
        """Dir del exe no escribible (p.ej. Program Files): OSError en _stage
        -> update_skip y 0, NUNCA propaga al refresher."""
        exe = b'NEW_EXE'
        sha = (hashlib.sha256(exe).hexdigest() + '  conflux.exe').encode()
        dl = {'https://x/exe': exe, 'https://x/sha': sha, 'https://x/sig': b'{}'}
        with patch.object(U, '_cfgget', return_value=True), \
                patch.object(U.sys, 'frozen', True, create=True), \
                patch.object(U, '_latest', return_value=_rel('v2.0.0')), \
                patch.object(U, '_dl', side_effect=lambda u, **_k: dl.get(u)), \
                patch.object(U, '_stage', side_effect=OSError('denied')), \
                patch.object(U, '_sig_verify', return_value='unavailable'), \
                patch.object(U, '_version', return_value='1.0.0'):
            self.assertEqual(U._update_tick(), 0)          # no lanza


class TestStage(unittest.TestCase):
    def test_escribe_marker_exe_ps1(self):
        import os
        import tempfile
        exe = b'EXE' * 100
        sha = hashlib.sha256(exe).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            new = os.path.join(td, 'conflux.new.exe')
            mj = os.path.join(td, 'u.json')
            ps1 = os.path.join(td, 'swap.ps1')
            with patch.object(U, '_NEW_EXE', new), \
                    patch.object(U, '_UPD_JSON', mj), \
                    patch.object(U, '_SWAP_PS1', ps1), \
                    patch.object(U.sys, 'executable', os.path.join(td, 'conflux.exe')):
                U._stage(exe, 'v2.0.0', 'unavailable')
                with open(new, 'rb') as f:
                    self.assertEqual(f.read(), exe)
                with open(mj, encoding='utf-8') as f:
                    marker = json.load(f)
                self.assertEqual(marker['tag'], 'v2.0.0')
                self.assertEqual(marker['sha256'], sha)
                self.assertTrue(os.path.exists(ps1))              # swap helper generado
                with open(ps1, encoding='utf-8-sig') as f:
                    body = f.read()
                self.assertIn(sha, body)                          # hash embebido
                self.assertIn('Get-FileHash', body)               # re-verifica
                self.assertIn('conflux.exe', body)                # proceso/exe real

    def test_ps1_escapa_comillas_y_unicode(self):
        """Path con ' (C:\\Users\\O'Brien) no rompe los literales PS;
        el archivo se escribe UTF-8-BOM (no OEM -> Unicode seguro)."""
        import os
        import tempfile
        exe = b'EXE'
        with tempfile.TemporaryDirectory() as td:
            weird = os.path.join(td, "O'Brien", 'conflux.exe')
            with patch.object(U, '_NEW_EXE', os.path.join(td, 'n.exe')), \
                    patch.object(U, '_UPD_JSON', os.path.join(td, 'u.json')), \
                    patch.object(U, '_SWAP_PS1', os.path.join(td, 's.ps1')), \
                    patch.object(U.sys, 'executable', weird):
                U._stage(exe, 'v2.0.0', 'unavailable')
                with open(os.path.join(td, 's.ps1'), 'rb') as f:
                    raw = f.read()
                self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))  # BOM utf-8-sig
                body = raw.decode('utf-8-sig')
                self.assertIn("O''Brien", body)                   # ' escapada
                self.assertIn("Move-Item $new $exe -Force -ErrorAction Stop",
                              body)                               # swap con retry

    def test_stage_posix_no_frozen_no_toca_executable(self):
        """POSIX no-frozen (dev/CI): marca+ps1, NUNCA os.replace sobre
        sys.executable — reemplazar el intérprete vivo sería catastrófico."""
        import os
        import tempfile
        exe = b'EXE'
        with tempfile.TemporaryDirectory() as td:
            marker = os.path.join(td, 'u.json')
            with patch.object(U.os, 'name', 'posix'), \
                    patch.object(U, '_NEW_EXE', os.path.join(td, 'n')), \
                    patch.object(U, '_UPD_JSON', marker), \
                    patch.object(U, '_SWAP_PS1', os.path.join(td, 's.ps1')), \
                    patch.object(U.sys, 'frozen', False, create=True):
                U._stage(exe, 'v2.0.0', 'unavailable')
            self.assertTrue(os.path.exists(marker))               # staged, no applied


if __name__ == '__main__':
    unittest.main()
