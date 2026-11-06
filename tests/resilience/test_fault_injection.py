"""Fault injection (CONFLUX-033): escrituras atómicas bajo fallos de disco,
config corrupta, JSON truncado — el estado persistido nunca queda peor."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from conflux.core import state as st  # noqa: E402


class TestAtomicWriteFaults(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "cfg.json")
        st._atomic_json(self.path, {"a": 1})

    def tearDown(self):
        self.dir.cleanup()

    def test_replace_fail_keeps_old(self):
        with mock.patch.object(st.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                st._atomic_json(self.path, {"a": 2})
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"a": 1})   # anterior intacto

    def test_fsync_fail_no_replace(self):
        with mock.patch.object(st.os, "fsync", side_effect=OSError("EIO")):
            with self.assertRaises(OSError):
                st._atomic_json(self.path, {"a": 2})
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"a": 1})

    def test_dirsync_fail_tolerated(self):
        # fsync de dir no soportado → solo el open del dir falla; write OK igual
        orig_open = st.os.open
        with mock.patch.object(st.os, "open",
                               side_effect=lambda *a, **k: orig_open(*a, **k)
                               if 'json' not in str(a[0]) else (_ for _ in ()).throw(OSError())):
            # el dirsync usa os.open sobre el DIRECTORIO ('.') — simular fallo
            with mock.patch.object(st.os, "open",
                                   side_effect=OSError("no dir fsync")):
                st._atomic_json(self.path, {"a": 3})
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"a": 3})

    def test_tmp_not_left_as_target(self):
        with mock.patch.object(st.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                st._atomic_json(self.path, {"a": 9})
        self.assertTrue(os.path.exists(self.path))


class TestCorruptState(unittest.TestCase):
    def test_load_manifests_cache_corrupt(self):
        import tempfile
        from conflux.domain import sources as src
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".manifests-cache.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write('{"trunc": [1,2')   # JSON truncado
            with mock.patch.object(src, "_MF_CACHE", p):
                self.assertIsNone(src._load_manifests_cache())  # corrupto → None, no crash

    def test_load_config_corrupt_json(self):
        import tempfile
        from conflux.core import cfg as cfgmod
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{{{not json")
            with mock.patch.object(cfgmod, "_CFG_FILE", p):
                cfgmod.load_config()           # no lanza → defaults
            self.assertEqual(st.CFG.get("max_connections"), 64)

    def test_config_future_version_rejected(self):
        import tempfile
        from conflux.core import cfg as cfgmod
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"config_version": 999, "max_connections": 7}, fh)
            with mock.patch.object(cfgmod, "_CFG_FILE", p):
                cfgmod.load_config()
            # versión futura no soportada → no se aplica el valor externo
            self.assertEqual(st.CFG.get("max_connections"), 64)


if __name__ == "__main__":
    unittest.main()
