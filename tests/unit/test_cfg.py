# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.core.cfg — config + perfiles."""
import io
import json
import os
import re
import unittest
from unittest import mock

from conflux.core import cfg


class TestLoadConfig(unittest.TestCase):
    def test_cfgget_default(self):
        self.assertEqual(cfg._cfgget('noexiste_clave_xyz', 42), 42)

    def test_es_pat_regex(self):
        pat = cfg._es_pat()
        self.assertTrue(pat.search('audio es dual'))
        self.assertFalse(re.search(pat, 'español'))  # \b exige palabra completa
        self.assertFalse(re.search(pat, 'english'))

    def test_re_es_existe(self):
        self.assertTrue(cfg.RE_ES.search('es'))
        self.assertFalse(cfg.RE_ES.search('en'))


class TestCoerce(unittest.TestCase):
    """_coerce normaliza tipos de config.json contra el default (edición a mano)."""

    def test_int_desde_str(self):
        self.assertEqual(cfg._coerce('calendar_months_ahead', '3'), 3)

    def test_int_basura_cae_al_default(self):
        self.assertEqual(cfg._coerce('calendar_months_ahead', 'abc'),
                         cfg._CFG_DEFAULTS['calendar_months_ahead'])

    def test_lista_desde_str_csv(self):
        self.assertEqual(cfg._coerce('stream_langs', 'es,en'), ['es', 'en'])

    def test_tipo_correcto_intocado(self):
        self.assertEqual(cfg._coerce('trakt_client_id', 'abc123'), 'abc123')
        self.assertEqual(cfg._coerce('stream_langs', ['es']), ['es'])

    def test_null_cae_al_default(self):
        self.assertEqual(cfg._coerce('m3u_path', None), '')      # "k":null no propaga None
        self.assertEqual(cfg._coerce('trakt_expires', None), 0)


class TestEffDisabled(unittest.TestCase):
    def test_eff_disabled_lista(self):
        self.assertIsInstance(cfg._eff_disabled(), set)


class TestLoadConfigFile(unittest.TestCase):
    """load_config sobre archivo: 'env:' refs, claves desconocidas, _save_cfg."""

    def _snapshot(self):
        return dict(cfg.CFG), dict(cfg._CFG_ENV_REF)

    def _restore(self, snap):
        old, old_ref = snap
        cfg.CFG.clear()
        cfg.CFG.update(old)
        cfg._CFG_ENV_REF.clear()
        cfg._CFG_ENV_REF.update(old_ref)

    def _load(self, data, env=None):
        with mock.patch.object(cfg, '_open',
                               return_value=io.StringIO(json.dumps(data))), \
                mock.patch.dict(os.environ, env or {}, clear=False):
            cfg.load_config()

    def test_env_ref_resuelve(self):
        """'env:VAR' lee el secreto del entorno — nada en claro en el archivo."""
        snap = self._snapshot()
        try:
            self._load({'debrid_key': 'env:DB_KEY'}, {'DB_KEY': 's3cr3t'})
            self.assertEqual(cfg.CFG['debrid_key'], 's3cr3t')
            self.assertEqual(cfg._CFG_ENV_REF['debrid_key'], ('DB_KEY', 's3cr3t'))
        finally:
            self._restore(snap)

    def test_env_var_ausente_vacio(self):
        snap = self._snapshot()
        try:
            self._load({'debrid_key': 'env:NO_EXISTE_XYZ'})
            self.assertEqual(cfg.CFG['debrid_key'], '')
        finally:
            self._restore(snap)

    def test_claves_desconocidas_log(self):
        """horizon_days y restos stale: se ignoran pero quedan registradas."""
        snap = self._snapshot()
        try:
            with mock.patch.object(cfg._log, 'info') as lg:
                self._load({'horizon_days': 9, 'debrid_key': 'k'})
            self.assertEqual(cfg.CFG['debrid_key'], 'k')
            logged = ' '.join(str(c) for c in lg.call_args_list)
            self.assertIn('horizon_days', logged)
            self.assertNotIn('debrid_key', logged)
        finally:
            self._restore(snap)


class TestSaveCfg(unittest.TestCase):
    def _saved(self):
        out = {}
        with mock.patch.object(cfg, '_atomic_json',
                               lambda _p, d: out.update(d)):
            cfg._save_cfg()
        return out

    def test_env_ref_reemite_no_secreto(self):
        snap = dict(cfg.CFG), dict(cfg._CFG_ENV_REF)
        try:
            cfg._CFG_ENV_REF['debrid_key'] = ('DB_KEY', 's3cr3t')
            cfg.CFG['debrid_key'] = 's3cr3t'
            self.assertEqual(self._saved()['debrid_key'], 'env:DB_KEY')
            cfg.CFG['debrid_key'] = 'otro'           # valor cambiado -> persiste el nuevo
            self.assertEqual(self._saved()['debrid_key'], 'otro')
        finally:
            self._restore_all(snap)

    def test_bool_false_persiste(self):
        """stream_prefetch=False es falsy pero debe guardarse (default True)."""
        snap = dict(cfg.CFG), dict(cfg._CFG_ENV_REF)
        try:
            cfg.CFG['stream_prefetch'] = False
            self.assertIs(self._saved()['stream_prefetch'], False)
        finally:
            self._restore_all(snap)

    def _restore_all(self, snap):
        old, old_ref = snap
        cfg.CFG.clear()
        cfg.CFG.update(old)
        cfg._CFG_ENV_REF.clear()
        cfg._CFG_ENV_REF.update(old_ref)


if __name__ == '__main__':
    unittest.main()
