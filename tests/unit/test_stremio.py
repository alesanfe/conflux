import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests import patch_fetch          # noqa: F401  (apaga red en imports)
from conflux.infra import stremio

CANARY = 'A' * 40 + '-canarytoken-secreto'


class TestValidKey(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(stremio._valid_key('aB3+/=_-' * 5))
        self.assertTrue(stremio._valid_key('x' * 20))
        self.assertTrue(stremio._valid_key('x' * 200))

    def test_invalid(self):
        for bad in ('', None, 'short', 'x' * 201, 'con espacios' + 'x' * 20,
                    'ñ' * 30, 'x' * 19):
            self.assertFalse(stremio._valid_key(bad), bad)


class TestProviderChain(unittest.TestCase):
    """Orden: env > config > keyring > scraping (opt-out)."""

    def setUp(self):
        self._env = dict(os.environ)
        for k in ('CONFLUX_STREMIO_AUTHKEY', 'CONFLUX_STREMIO_SCRAPE'):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_env_wins(self):
        os.environ['CONFLUX_STREMIO_AUTHKEY'] = 'e' * 30
        with mock.patch.object(stremio, '_authkey_leveldb',
                               return_value='l' * 30) as lv:
            key, prov = stremio.find_auth()
        self.assertEqual((key, prov), ('e' * 30, 'env'))
        lv.assert_not_called()                    # no toca disco si hay env

    def test_config_beats_scrape(self):
        with mock.patch.object(stremio, '_key_cfg', return_value='c' * 30), \
             mock.patch.object(stremio, '_authkey_leveldb') as lv, \
             mock.patch.object(stremio, '_authkey_sqlite') as sq:
            key, prov = stremio.find_auth()
        self.assertEqual((key, prov), ('c' * 30, 'config'))
        lv.assert_not_called()
        sq.assert_not_called()

    def test_scrape_disabled_env(self):
        os.environ['CONFLUX_STREMIO_SCRAPE'] = '0'
        with mock.patch.object(stremio, '_key_cfg', return_value=None), \
             mock.patch.object(stremio, '_key_keyring', return_value=None), \
             mock.patch.object(stremio, '_authkey_leveldb',
                               return_value='l' * 30) as lv, \
             mock.patch.object(stremio, '_authkey_sqlite',
                               return_value='s' * 30) as sq:
            self.assertEqual(stremio.find_auth(), (None, None))
        lv.assert_not_called()
        sq.assert_not_called()

    def test_scrape_disabled_cfg(self):
        import sys
        cfg_mod = sys.modules['conflux.core.cfg']
        old = dict(getattr(cfg_mod._PROF, 'cfg', None) or {})
        cfg_mod._PROF.cfg = {'authkey_scrape': False}
        try:
            with mock.patch.object(stremio, '_key_keyring',
                                   return_value=None), \
                 mock.patch.object(stremio, '_authkey_leveldb') as lv, \
                 mock.patch.object(stremio, '_authkey_sqlite') as sq:
                self.assertEqual(stremio.find_auth(), (None, None))
            lv.assert_not_called()
            sq.assert_not_called()
        finally:
            cfg_mod._PROF.cfg = old or None

    def test_invalid_candidates_rejected(self):
        """Un patrón con forma de clave pero inválido no se acepta."""
        with mock.patch.object(stremio, '_key_cfg', return_value='mal!'), \
             mock.patch.object(stremio, '_key_keyring', return_value=None), \
             mock.patch.object(stremio, '_authkey_leveldb', return_value=None), \
             mock.patch.object(stremio, '_authkey_sqlite',
                               return_value='s' * 30):
            self.assertEqual(stremio.find_auth(), ('s' * 30, 'sqlite'))


class TestLeveldbScrape(unittest.TestCase):
    def _db_dir(self, tmp):
        d = os.path.join(tmp, 'prof', 'Local Storage', 'leveldb')
        os.makedirs(d)
        return d

    def _write_hit(self, d, name, key=CANARY):
        p = os.path.join(d, name)
        with open(p, 'wb') as fh:
            fh.write(b'\x00' * 10 + b'uthKey\x01\x02"' + key.encode()
                     + b'"' + b'\xff' * 5)
        return p

    def test_finds_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._db_dir(tmp)
            self._write_hit(d, '000003.log')
            with mock.patch.object(stremio, '_leveldb_dirs',
                                   return_value=[d]):
                self.assertEqual(stremio._authkey_leveldb(), CANARY)

    def test_symlink_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._db_dir(tmp)
            target = self._write_hit(tmp, 'fuera.log')
            link = os.path.join(d, '000003.log')
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest('sin symlinks')
            with mock.patch.object(stremio, '_leveldb_dirs',
                                   return_value=[d]):
                self.assertIsNone(stremio._authkey_leveldb())

    def test_oversize_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._db_dir(tmp)
            p = self._write_hit(d, 'big.log')
            with open(p, 'ab') as fh:
                fh.write(b'\x00' * 200)
            with mock.patch.object(stremio, '_leveldb_dirs', return_value=[d]), \
                 mock.patch.object(stremio, '_MAX_FILE_BYTES', 100):
                self.assertGreater(os.path.getsize(p), 100)
                self.assertIsNone(stremio._authkey_leveldb())

    def test_invalid_hit_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._db_dir(tmp)
            self._write_hit(d, '000003.log', key='k' * 201)   # fuera de rango
            with mock.patch.object(stremio, '_leveldb_dirs',
                                   return_value=[d]):
                self.assertIsNone(stremio._authkey_leveldb())

    def test_garbage_files_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._db_dir(tmp)
            with open(os.path.join(d, 'a.ldb'), 'wb') as fh:
                fh.write(os.urandom(4096))
            self._write_hit(d, 'b.log')
            with mock.patch.object(stremio, '_leveldb_dirs',
                                   return_value=[d]):
                self.assertEqual(stremio._authkey_leveldb(), CANARY)


class TestSqlite(unittest.TestCase):
    def test_corrupt_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'localstorage.sqlite3')
            with open(bad, 'wb') as fh:
                fh.write(b'no es sqlite' * 100)
            with mock.patch.object(stremio.glob, 'glob', return_value=[bad]):
                self.assertIsNone(stremio.find_db())
                self.assertIsNone(stremio._authkey_sqlite())

    def test_truncated_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'localstorage.sqlite3')
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
            c.execute("INSERT INTO ItemTable VALUES ('profile', ?)",
                      (b'\x00\xff{romp',))
            c.commit()
            c.close()
            with mock.patch.object(stremio, 'find_db', return_value=db):
                self.assertIsNone(stremio._authkey_sqlite())


class TestErrorScrub(unittest.TestCase):
    def test_safe_err(self):
        self.assertNotIn(CANARY, stremio._safe_err(
            RuntimeError(f'403 bad authKey={CANARY}'), CANARY))
        self.assertIn('***', stremio._safe_err(
            RuntimeError(CANARY), CANARY))

    def test_install_remote_no_key_in_error(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get',
                               side_effect=RuntimeError(f'denied {CANARY}')):
            st, msg = stremio.install_remote()
        self.assertEqual(st, 'error')
        self.assertNotIn(CANARY, msg)

    def test_remove_remote_no_key_in_error(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get',
                               side_effect=RuntimeError(f'{CANARY} expired')):
            st, msg = stremio.remove_remote()
        self.assertEqual(st, 'error')
        self.assertNotIn(CANARY, msg)

    def test_install_reports_provider(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get', return_value=[]), \
             mock.patch.object(stremio.urllib.request, 'urlopen',
                               return_value=mock.Mock(
                                   read=lambda: b'{}')), \
             mock.patch.object(stremio, '_api_set'), \
             mock.patch.object(stremio.json, 'load', return_value={'id': 'x'}):
            st, msg = stremio.install_remote()
        self.assertEqual(st, 'installed')
        self.assertIn('env', msg)            # proveedor logueado, clave no
        self.assertNotIn(CANARY, msg)


class TestLeveldbDirs(unittest.TestCase):
    def test_descubre_dirs_conocidos(self):
        with tempfile.TemporaryDirectory() as home:
            la = os.path.join(home, 'LA')
            ra = os.path.join(home, 'RA')
            d1 = os.path.join(la, 'Smart Code ltd', 'Stremio', 'prof',
                              'Local Storage', 'leveldb')
            d2 = os.path.join(ra, 'stremio5', 'leveldb')
            os.makedirs(d1)
            os.makedirs(d2)
            env = dict(os.environ)
            os.environ['LOCALAPPDATA'] = la
            os.environ['APPDATA'] = ra
            try:
                with mock.patch.object(stremio.os.path, 'expanduser',
                                       side_effect=lambda p:
                                       p.replace('~', home)):
                    out = stremio._leveldb_dirs()
            finally:
                os.environ.clear()
                os.environ.update(env)
        self.assertIn(d1, out)
        self.assertIn(d2, out)

    def test_sin_dirs_lista_vacia(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            os.environ['LOCALAPPDATA'] = home
            os.environ['APPDATA'] = home
            try:
                with mock.patch.object(stremio.os.path, 'expanduser',
                                       return_value=home):
                    self.assertEqual(stremio._leveldb_dirs(), [])
            finally:
                os.environ.clear()
                os.environ.update(env)


class TestLeveldbScrapeEdge(unittest.TestCase):
    def _db_dir(self, tmp):
        d = os.path.join(tmp, 'prof', 'Local Storage', 'leveldb')
        os.makedirs(d)
        return d

    def test_glob_oserror_continua(self):
        with mock.patch.object(stremio, '_leveldb_dirs',
                               return_value=['/dir1', '/dir2']), \
             mock.patch.object(stremio.glob, 'glob',
                               side_effect=OSError('perm')):
            self.assertIsNone(stremio._authkey_leveldb())

    def test_read_oserror_continua(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._db_dir(tmp)
            p = os.path.join(d, 'a.log')
            with open(p, 'wb') as fh:
                fh.write(b'x')
            with mock.patch.object(stremio, '_leveldb_dirs',
                                   return_value=[d]), \
                 mock.patch('builtins.open', side_effect=OSError('ro')):
                self.assertIsNone(stremio._authkey_leveldb())

    def test_budget_de_tiempo_corta(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._db_dir(tmp)
            with open(os.path.join(d, 'a.log'), 'wb') as fh:
                fh.write(b'x')
            it = iter([0.0, 999.0])          # t0=0, luego ya pasó el budget
            with mock.patch.object(stremio, '_leveldb_dirs',
                                   return_value=[d]), \
                 mock.patch('conflux.core.state.CLOCK') as clk:
                clk.time.side_effect = lambda: next(it)
                self.assertIsNone(stremio._authkey_leveldb())


class TestLeveldbHitOrder(unittest.TestCase):
    def test_ultimo_hit_gana(self):
        """Dos claves válidas en el mismo fichero -> la ÚLTIMA es la más
        reciente y la que se devuelve (reversed(hits))."""
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, 'prof', 'Local Storage', 'leveldb')
            os.makedirs(d)
            vieja = 'v' * 40
            nueva = 'n' * 40
            p = os.path.join(d, 'a.log')
            with open(p, 'wb') as fh:
                fh.write(b'uthKey\x01"' + vieja.encode() + b'"')
                fh.write(b'xxx' * 20)
                fh.write(b'uthKey\x02"' + nueva.encode() + b'"')
            with mock.patch.object(stremio, '_leveldb_dirs',
                                   return_value=[d]):
                self.assertEqual(stremio._authkey_leveldb(), nueva)


class TestFindDbEdge(unittest.TestCase):
    def test_db_valida_devuelve_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 's.sqlite3')
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
            c.execute("INSERT INTO ItemTable VALUES ('profile', ?)",
                      (b'{}',))
            c.commit()
            c.close()
            with mock.patch.object(stremio.glob, 'glob', return_value=[db]):
                self.assertEqual(stremio.find_db(), db)

    def test_db_sin_profile_sigue_buscando(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 's.sqlite3')
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
            c.commit()
            c.close()
            with mock.patch.object(stremio.glob, 'glob', return_value=[db]):
                self.assertIsNone(stremio.find_db())

    def test_primera_rota_segunda_valida(self):
        """Candidata corrupta -> except+close -> siguiente (118->109)."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.sqlite3')
            with open(bad, 'wb') as fh:
                fh.write(b'no sqlite' * 50)
            good = os.path.join(tmp, 'good.sqlite3')
            c = sqlite3.connect(good)
            c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
            c.execute("INSERT INTO ItemTable VALUES ('profile', ?)",
                      (b'{}',))
            c.commit()
            c.close()
            with mock.patch.object(stremio.glob, 'glob',
                                   return_value=[bad, good]), \
                 mock.patch.object(stremio.os.path, 'getmtime',
                                   side_effect=lambda p:
                                   2 if 'good' in p else 1):
                self.assertEqual(stremio.find_db(), good)

    def test_close_error_tragado(self):
        """close() que lanza sqlite.Error no tumba find_db (121-122)."""
        conn = mock.MagicMock()
        conn.execute.side_effect = sqlite3.Error('x')
        conn.close.side_effect = sqlite3.Error('close boom')
        with mock.patch.object(stremio.glob, 'glob',
                               return_value=['db1']), \
             mock.patch.object(stremio.os.path, 'getmtime',
                               return_value=1), \
             mock.patch.object(stremio.sqlite3, 'connect',
                               return_value=conn):
            self.assertIsNone(stremio.find_db())

    def test_sqlite_update_sin_db_false(self):
        with mock.patch.object(stremio, 'find_db', return_value=None):
            self.assertFalse(stremio._sqlite_update(lambda a: a))

    def test_install_remote_continua_otros_addons(self):
        """Addons ajenos se saltan en el bucle y el nuestro se añade (247->246)."""
        man = {'id': 'x'}
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get',
                               return_value=[{'transportUrl': 'http://otro/a'},
                                             {'transportUrl': 'http://otro/b'}]), \
             mock.patch.object(stremio.urllib.request, 'urlopen'), \
             mock.patch.object(stremio.json, 'load', return_value=man), \
             mock.patch.object(stremio, '_api_set') as api_set:
            st, _ = stremio.install_remote()
        self.assertEqual(st, 'installed')
        self.assertEqual(len(api_set.call_args[0][1]), 3)

    def test_authkey_sqlite_valida(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 's.sqlite3')
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
            prof = {'auth': {'key': 'k' * 40}}
            c.execute("INSERT INTO ItemTable VALUES ('profile', ?)",
                      (json.dumps(prof).encode('utf-16-le'),))
            c.commit()
            c.close()
            with mock.patch.object(stremio, 'find_db', return_value=db):
                self.assertEqual(stremio._authkey_sqlite(), 'k' * 40)


class TestProvidersEdge(unittest.TestCase):
    def test_key_cfg_excepcion_none(self):
        import conflux.core.cfg as cfg
        with mock.patch.object(cfg, '_cfgget', side_effect=RuntimeError()):
            self.assertIsNone(stremio._key_cfg())

    def test_key_keyring_none_sin_modulo(self):
        import sys
        with mock.patch.dict(sys.modules, {'keyring': None}):
            self.assertIsNone(stremio._key_keyring())

    def test_key_keyring_ok(self):
        import sys
        kr = mock.MagicMock()
        kr.get_password.return_value = 'k' * 40
        with mock.patch.dict(sys.modules, {'keyring': kr}):
            self.assertEqual(stremio._key_keyring(), 'k' * 40)

    def test_scrape_enabled_cfg_explota_cae_a_env(self):
        import conflux.core.cfg as cfg
        env = dict(os.environ)
        os.environ['CONFLUX_STREMIO_SCRAPE'] = '0'
        try:
            with mock.patch.object(cfg, '_cfgget',
                                   side_effect=RuntimeError()):
                self.assertFalse(stremio._scrape_enabled())
        finally:
            os.environ.clear()
            os.environ.update(env)

    def test_find_auth_provider_explota_sigue(self):
        with mock.patch.object(stremio, '_key_env', side_effect=KeyError()), \
             mock.patch.object(stremio, '_key_cfg', return_value='c' * 30), \
             mock.patch.object(stremio, '_scrape_enabled', return_value=False):
            self.assertEqual(stremio.find_auth(), ('c' * 30, 'config'))

    def test_find_authkey_atajo(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=('k' * 30, 'env')):
            self.assertEqual(stremio.find_authkey(), 'k' * 30)


class TestApi(unittest.TestCase):
    def test_api_result_ok(self):
        res = mock.MagicMock()
        with mock.patch.object(stremio.urllib.request, 'urlopen',
                               return_value=res), \
             mock.patch.object(stremio.json, 'load',
                               return_value={'result': {'x': 1}}):
            self.assertEqual(stremio._api('m', {}), {'x': 1})

    def test_api_error_lanza(self):
        res = mock.MagicMock()
        with mock.patch.object(stremio.urllib.request, 'urlopen',
                               return_value=res), \
             mock.patch.object(stremio.json, 'load',
                               return_value={'error': 'denied'}):
            with self.assertRaises(RuntimeError):
                stremio._api('m', {})

    def test_api_get_devuelve_addons(self):
        with mock.patch.object(stremio, '_api',
                               return_value={'addons': [{'a': 1}]}):
            self.assertEqual(stremio._api_get('k'), [{'a': 1}])

    def test_api_get_result_none_vacia(self):
        with mock.patch.object(stremio, '_api', return_value=None):
            self.assertEqual(stremio._api_get('k'), [])

    def test_api_set_llama(self):
        with mock.patch.object(stremio, '_api') as a:
            stremio._api_set('k', [1])
        a.assert_called_once_with('addonCollectionSet',
                                  {'authKey': 'k', 'addons': [1]})


class TestInstallRemoveRemote(unittest.TestCase):
    def test_install_manual_sin_key(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(None, None)):
            st, msg = stremio.install_remote()
        self.assertEqual(st, 'manual')

    def test_install_already_mismo_manifest(self):
        man = {'id': 'x'}
        addons = [{'transportUrl': stremio.PROXY_MANIFEST, 'manifest': man}]
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get', return_value=addons), \
             mock.patch.object(stremio.urllib.request, 'urlopen'), \
             mock.patch.object(stremio.json, 'load', return_value=man), \
             mock.patch.object(stremio, '_api_set') as api_set:
            st, msg = stremio.install_remote()
        self.assertEqual(st, 'already')
        api_set.assert_not_called()           # idéntico: nada que escribir

    def test_install_updated_manifest_viejo(self):
        addons = [{'transportUrl': stremio.PROXY_MANIFEST,
                   'manifest': {'id': 'viejo'}}]
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get', return_value=addons), \
             mock.patch.object(stremio.urllib.request, 'urlopen'), \
             mock.patch.object(stremio.json, 'load',
                               return_value={'id': 'nuevo'}), \
             mock.patch.object(stremio, '_api_set') as api_set:
            st, msg = stremio.install_remote()
        self.assertEqual(st, 'updated')
        api_set.assert_called_once()
        self.assertEqual(api_set.call_args[0][1][0]['manifest'],
                         {'id': 'nuevo'})    # snapshot refrescado in-situ

    def test_remove_manual_sin_key(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(None, None)):
            self.assertEqual(stremio.remove_remote()[0], 'manual')

    def test_remove_absent_no_escribe(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get',
                               return_value=[{'transportUrl': 'otro'}]), \
             mock.patch.object(stremio, '_api_set') as api_set:
            st, _ = stremio.remove_remote()
        self.assertEqual(st, 'absent')
        api_set.assert_not_called()

    def test_remove_ok(self):
        with mock.patch.object(stremio, 'find_auth',
                               return_value=(CANARY, 'env')), \
             mock.patch.object(stremio, '_api_get',
                               return_value=[{'transportUrl':
                                              stremio.PROXY_MANIFEST}]), \
             mock.patch.object(stremio, '_api_set') as api_set:
            st, _ = stremio.remove_remote()
        self.assertEqual(st, 'removed')
        api_set.assert_called_once_with(CANARY, [])


class TestInstallLocal(unittest.TestCase):
    def test_install_remoto_mas_local(self):
        with mock.patch.object(stremio, 'install_remote',
                               return_value=('installed', 'ok')), \
             mock.patch.object(stremio, 'find_db', return_value='/db'), \
             mock.patch.object(stremio.urllib.request, 'urlopen'), \
             mock.patch.object(stremio.json, 'load', return_value={'id': 1}), \
             mock.patch.object(stremio, '_sqlite_update', return_value=True):
            st, msg = stremio.install()
        self.assertEqual(st, 'installed')
        self.assertIn('perfil local', msg)

    def test_install_already_mas_local_sync(self):
        with mock.patch.object(stremio, 'install_remote',
                               return_value=('already', 'x')), \
             mock.patch.object(stremio, 'find_db', return_value='/db'), \
             mock.patch.object(stremio.urllib.request, 'urlopen'), \
             mock.patch.object(stremio.json, 'load', return_value={}), \
             mock.patch.object(stremio, '_sqlite_update', return_value=True):
            st, msg = stremio.install()
        self.assertEqual(st, 'already')
        self.assertIn('local sync', msg)

    def test_install_remoto_falla_local_salva(self):
        """Sin sesión remota pero con sqlite local -> installed vía local."""
        with mock.patch.object(stremio, 'install_remote',
                               return_value=('manual', 'sin key')), \
             mock.patch.object(stremio, 'find_db', return_value='/db'), \
             mock.patch.object(stremio.urllib.request, 'urlopen'), \
             mock.patch.object(stremio.json, 'load', return_value={}), \
             mock.patch.object(stremio, '_sqlite_update', return_value=True):
            st, msg = stremio.install()
        self.assertEqual(st, 'installed')
        self.assertIn('perfil local', msg)

    def test_install_sin_remoto_ni_local_pasa_estado(self):
        with mock.patch.object(stremio, 'install_remote',
                               return_value=('manual', 'sin key')), \
             mock.patch.object(stremio, 'find_db', return_value=None):
            st, _ = stremio.install()
        self.assertEqual(st, 'manual')

    def test_fetch_manifest_local_falla_no_tumba_remoto(self):
        """El 2º urlopen (manifest para sqlite) iba sin try: un fallo puntual
        mataba el registro aunque el remoto ya se hubiera aplicado."""
        with mock.patch.object(stremio, 'install_remote',
                               return_value=('installed', 'ok')), \
             mock.patch.object(stremio, 'find_db', return_value='/db'), \
             mock.patch.object(stremio.urllib.request, 'urlopen',
                               side_effect=OSError('caído')):
            st, msg = stremio.install()
        self.assertEqual(st, 'installed')
        self.assertNotIn('perfil local', msg)

    def test_remove_remoto_y_local_besteffort(self):
        with mock.patch.object(stremio, 'remove_remote',
                               return_value=('removed', 'ok')), \
             mock.patch.object(stremio, '_sqlite_update',
                               side_effect=Exception('bloqueado')):
            st, _ = stremio.remove()
        self.assertEqual(st, 'removed')      # sqlite error no invalida


class TestManifestWriteLocal(unittest.TestCase):
    def test_sqlite_update_blob_utf16(self):
        """R6: el perfil se escribe como BLOB UTF-16LE, nunca TEXT UTF-8."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 's.sqlite3')
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
            prof = {'addons': [], 'auth': {}}
            c.execute("INSERT INTO ItemTable VALUES ('profile', ?)",
                      (json.dumps(prof).encode('utf-16-le'),))
            c.commit()
            c.close()
            with mock.patch.object(stremio, 'find_db', return_value=db):
                self.assertTrue(stremio._sqlite_update(
                    lambda ads: ads + [{'transportUrl': 'u'}]))
            c = sqlite3.connect(db)
            raw = c.execute("SELECT value FROM ItemTable WHERE key='profile'"
                            ).fetchone()[0]
            c.close()
            self.assertIsInstance(raw, bytes)
            self.assertEqual(json.loads(raw.decode('utf-16-le'))
                             ['addons'][0]['transportUrl'], 'u')


if __name__ == '__main__':
    unittest.main()
