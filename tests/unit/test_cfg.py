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

    def test_clave_sin_default_pasa_intacta(self):
        """d is None -> return v: clave fuera de _CFG_DEFAULTS no se toca
        (mutante Or@29: caería a `return d` -> None)."""
        self.assertEqual(cfg._coerce('clave_que_no_existe', 'abc'), 'abc')
        self.assertEqual(cfg._coerce('clave_que_no_existe', 7), 7)


class TestEffDisabled(unittest.TestCase):
    def test_eff_disabled_lista(self):
        self.assertIsInstance(cfg._eff_disabled(), set)


class TestCoerceBool(unittest.TestCase):
    def test_bool_desde_str(self):
        """'true'/'1'/'si' -> True; resto -> False (In@33: notin invertiría)."""
        self.assertIs(cfg._coerce('stream_prefetch', 'true'), True)
        self.assertIs(cfg._coerce('stream_prefetch', '1'), True)
        self.assertIs(cfg._coerce('stream_prefetch', '0'), False)
        self.assertIs(cfg._coerce('stream_prefetch', 'nope'), False)

    def test_bool_pasa_intocado(self):
        self.assertIs(cfg._coerce('stream_prefetch', False), False)

    def test_str_desde_int(self):
        """Default str + valor int -> str(v) (rama isinstance(d,str))."""
        self.assertEqual(cfg._coerce('trakt_client_id', 123), '123')

    def test_lista_desde_tupla_y_basura(self):
        self.assertEqual(cfg._coerce('stream_langs', ('es', 'en')), ['es', 'en'])
        self.assertEqual(cfg._coerce('stream_langs', 42), ['42'])  # str().split(',')


class TestLoadDerivations(unittest.TestCase):
    """load_config re-deriva _DISABLED / RE_ES / SOURCES — gates `or`/guards."""

    def _snapshot(self):
        return (dict(cfg.CFG), list(cfg.SOURCES), set(cfg._DISABLED),
                cfg.RE_ES, cfg.RE_EX)

    def _restore(self, snap):
        old, srcs, dis, re_es, re_ex = snap
        cfg.CFG.clear(); cfg.CFG.update(old)
        cfg.SOURCES[:] = srcs
        cfg._DISABLED.clear(); cfg._DISABLED.update(dis)
        cfg.RE_ES, cfg.RE_EX = re_es, re_ex

    def _load(self, data):
        with mock.patch.object(cfg, '_open',
                               return_value=io.StringIO(json.dumps(data))), \
                mock.patch.object(cfg, 'maybe_provision', create=True), \
                mock.patch('conflux.infra.mediafusion.maybe_provision'):
            cfg.load_config()

    def test_sources_disabled_deriva(self):
        """sources_disabled=['X'] puebla _DISABLED (Or@67: and -> [])."""
        snap = self._snapshot()
        try:
            self._load({'sources_disabled': ['Torrentio']})
            self.assertIn('Torrentio', cfg._DISABLED)
        finally:
            self._restore(snap)

    def test_langs_vacios_recae_es(self):
        """stream_langs=[] -> `or ['es']` (Or@68: and -> '' -> \\b()\\b lo
        casa todo)."""
        snap = self._snapshot()
        try:
            self._load({'stream_langs': []})
            self.assertTrue(cfg.RE_ES.search('pista es'))
            self.assertFalse(cfg.RE_ES.search('latam'))   # patrón solo 'es'
        finally:
            self._restore(snap)

    def test_extra_sources_normaliza_agrega_dedup(self):
        """extra_sources: quita /manifest.json y slash final, nombre=host, no
        duplica una base ya existente (Or@81, NotEq/And@86, Add@88)."""
        from conflux.core.state import _SOURCES_BASE
        snap = self._snapshot()
        base0 = list(_SOURCES_BASE)
        try:
            self._load({'extra_sources': [
                'https://extra.com/manifest.json',
                'https://extra.com/',                       # dup tras normalizar
                base0[0].base,                              # dup de la base
                'ftp://sin-http.com',                       # esquema malo
                'http:///sin-host'                          # host None
            ]})
            extra = [s for s in cfg.SOURCES if s not in base0]
            self.assertEqual([(s.name, s.base) for s in extra],
                             [('extra.com', 'https://extra.com')])
        finally:
            self._restore(snap)


class TestExRe(unittest.TestCase):
    def test_corto_borde_largo_substring(self):
        """<=3 chars -> borde alfanumérico; >3 -> substring (Gt@100, 3+1)."""
        p = cfg._ex_re(['ts'])
        self.assertFalse(p.search('cats boots'))           # 'ts' dentro de palabra no
        self.assertTrue(p.search('1080 ts rip'))
        q = cfg._ex_re(['cats'])                            # len 4 -> substring
        self.assertTrue(q.search('scats'))

    def test_multitermino_y_vacio(self):
        """'(?:' + join + ')' compone patrón único (Add@101); términos -> match
        (Or@98: and -> []); vacío/None -> nunca casa."""
        p = cfg._ex_re(['cam', 'screener'])
        self.assertTrue(p.search('x cam x'))
        self.assertTrue(p.search('SCREENER'))
        self.assertFalse(p.search('camino'))                # 'cam' exige borde
        self.assertFalse(cfg._ex_re([]).search('cam'))
        self.assertFalse(cfg._ex_re(None).search('cam'))


class TestProfiles(unittest.TestCase):
    def _prof(self, d):
        cfg._PROF.cfg = d
        self.addCleanup(lambda: delattr(cfg._PROF, 'cfg'))

    def test_profile_cache_identidad(self):
        """name in _PROFILES -> cache hit, mismo dict (NotIn@142 recargaría)."""
        with mock.patch.object(cfg, '_open', side_effect=AssertionError('disk')):
            cfg._PROFILES['__t'] = {'x': 1}
            try:
                self.assertIs(cfg._profile_load('__t'), cfg._PROFILES['__t'])
            finally:
                cfg._PROFILES.pop('__t', None)

    def test_profile_cap_32_eviccion(self):
        """len>=32 desaloja el más viejo antes de insertar (32+1, GtE@143)."""
        snap = dict(cfg._PROFILES)
        try:
            cfg._PROFILES.clear()
            for i in range(32):
                cfg._PROFILES[f'p{i}'] = {}
            with mock.patch.object(cfg, '_open',
                                   return_value=io.StringIO('{"k":1}')):
                cfg._profile_load('nuevo')
            self.assertEqual(len(cfg._PROFILES), 32)
            self.assertIn('nuevo', cfg._PROFILES)
            self.assertNotIn('p0', cfg._PROFILES)           # el más viejo salió
        finally:
            cfg._PROFILES.clear(); cfg._PROFILES.update(snap)

    def test_cfgget_override_y_tipos(self):
        """Perfil pisa global solo con tipo compatible (In@161, Is/Or/And@163-164)."""
        self._prof({'stream_sort': ['seeds'], 'calendar_months_ahead': 'malo',
                    'stream_max_gb': 7})
        with mock.patch.dict(cfg.CFG, {'stream_sort': ['res'],
                                       'calendar_months_ahead': 2,
                                       'stream_max_gb': 0}):
            self.assertEqual(cfg._cfgget('stream_sort'), ['seeds'])
            self.assertEqual(cfg._cfgget('calendar_months_ahead'), 2)  # tipo malo -> global
            self.assertEqual(cfg._cfgget('stream_max_gb'), 7)          # int ok
            self.assertEqual(cfg._cfgget('stream_prefetch', True), True)  # no en perfil -> global

    def test_eff_disabled_perfil(self):
        """sources_disabled de perfil sustituye al global (In@171 + _strs)."""
        self._prof({'sources_disabled': ['A', 'B']})
        self.assertEqual(cfg._eff_disabled(), {'A', 'B'})
        del cfg._PROF.cfg['sources_disabled']
        cfg._PROF.cfg['sources_disabled'] = 'no-lista'      # tipo malo -> global
        self.assertIs(cfg._eff_disabled(), cfg._DISABLED)

    def test_groups_cfg_perfil_y_global(self):
        """source_groups: perfil si es dict, si no global (In/And@177, Or@179)."""
        self._prof({'source_groups': {'movie': ['A']}})
        self.assertEqual(cfg._groups_cfg(), {'movie': ['A']})
        cfg._PROF.cfg['source_groups'] = 'no-dict'
        with mock.patch.dict(cfg.CFG, {'source_groups': {'series': ['B']}}):
            self.assertEqual(cfg._groups_cfg(), {'series': ['B']})

    def test_eff_disabled_for_grupo(self):
        """Tipo con grupo -> solo sus fuentes responden (IsNot@186, NotIn@188)."""
        from conflux.core.state import Source
        old = list(cfg.SOURCES), set(cfg._DISABLED)
        try:
            cfg.SOURCES[:] = [Source('A', 'u1'), Source('B', 'u2'),
                              Source('C', 'u3')]
            cfg._DISABLED.clear(); cfg._DISABLED.update({'C'})
            with mock.patch.dict(cfg.CFG,
                                 {'source_groups': {'movie': ['A']}}):
                dis = cfg._eff_disabled_for('movie')
                self.assertIn('B', dis)                     # fuera del grupo
                self.assertIn('C', dis)                     # disabled global suma
                self.assertNotIn('A', dis)
                self.assertNotIn('B', cfg._eff_disabled_for('series'))  # sin grupo
        finally:
            cfg.SOURCES[:] = old[0]
            cfg._DISABLED.clear(); cfg._DISABLED.update(old[1])

    def test_es_pat_perfil(self):
        """stream_langs de perfil compila su regex (Add@203s, _strs)."""
        self._prof({'stream_langs': ['de', 'vo']})
        p = cfg._es_pat()
        self.assertTrue(p.search('audio de'))
        self.assertFalse(p.search('audio es'))
        cfg._PROF.cfg['stream_langs'] = 9                    # tipo malo -> RE_ES
        self.assertIs(cfg._es_pat(), cfg.RE_ES)

    def test_ex_pat_perfil(self):
        """stream_exclude de perfil -> su regex; sin clave -> RE_EX global
        (Or@209, IsNot@210)."""
        self._prof({'stream_exclude': ['zzz']})
        p = cfg._ex_pat()
        self.assertTrue(p.search('xxx zzz'))
        self.assertIsNot(p, cfg.RE_EX)
        del cfg._PROF.cfg['stream_exclude']
        self.assertIs(cfg._ex_pat(), cfg.RE_EX)


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
