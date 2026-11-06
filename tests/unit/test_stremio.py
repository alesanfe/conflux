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
