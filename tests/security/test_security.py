# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Security tests: XML untrusted, traversal, redirects, fugas de secretos."""
import os
import re
import unittest
from unittest.mock import patch

from defusedxml.common import EntitiesForbidden

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'src', 'conflux')


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


class TestXmlSeguro(unittest.TestCase):
    """Torznab parsea XML de indexers no confiables — nunca stdlib."""

    def test_no_stdlib_elementtree_en_fuentes(self):
        for root, _, files in os.walk(SRC):
            for f in files:
                if not f.endswith('.py'):
                    continue
                code = _read(os.path.join(root, f))
                self.assertNotIn('import xml.etree', code,
                                 f'{f}: stdlib ElementTree prohibido')
                self.assertNotIn('from xml.etree', code, f'{f}: stdlib ElementTree prohibido')

    def test_defusedxml_guardado(self):
        import conflux.infra.torznab as tz
        if tz._ET is None:
            self.skipTest('defusedxml no instalado — torznab desactivado (by design)')
        # XXE: defusedxml rechaza entities externas
        xxe = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
               b'<rss><item><title>&x;</title></item></rss>')
        with self.assertRaises(EntitiesForbidden):
            tz._ET.fromstring(xxe)

    def test_billion_laughs_rechazado(self):
        import conflux.infra.torznab as tz
        if tz._ET is None:
            self.skipTest('defusedxml no instalado')
        bomb = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "xxxx">'
                b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><r>&c;</r>')
        with self.assertRaises(EntitiesForbidden):
            tz._ET.fromstring(bomb)


class TestPathTraversal(unittest.TestCase):
    def test_perfil_nombre_solo_seguro(self):
        """El nombre de perfil jamás contiene .. — solo [a-zA-Z0-9_-]{1,24}."""
        import conflux.web.server as srv
        h = srv.H.__new__(srv.H)
        self.assertEqual(h._profile('/p/../x'), '/p/../x')     # no match: sin perfil
        self.assertEqual(h._profile('/p/' + 'a' * 40 + '/x'), '/p/' + 'a' * 40 + '/x')
        self.assertEqual(h._profile('/p/ok/x'), '/x')          # nombre sano: perfil carga

    def test_traversal_en_ids_muere_en_routing(self):
        """'..' en cid/sid: quoted a upstream o rechazado por routing — nunca FS."""
        import conflux.domain.catalog as cat
        from conflux.app import meta
        from tests import patch_fetch
        self.assertEqual(cat.handle_catalog('movie', '..', None), {'metas': []})
        restore = patch_fetch(lambda u, t=8: None)  # hermetico: sin red real
        try:
            r = meta.handle_meta('movie', '..%2f..%2fetc%2fpasswd')
        finally:
            restore()
        self.assertIn('meta', r)


class TestRedirects(unittest.TestCase):
    def test_db_resolve_desconocido_none(self):
        import conflux.infra.debrid as db
        self.assertIsNone(db._db_resolve('provider_inventado', 'h' * 10, 0))

    def test_sub_download_sin_key_none(self):
        import conflux.app.subs as s
        from conflux.core import cfg
        with patch.object(cfg, '_cfgget', return_value=None):
            self.assertIsNone(s._sub_download('12345'))


class TestSecretos(unittest.TestCase):
    def test_reload_enmascara_keys(self):
        """POST /reload devuelve config con secretos enmascarados (key/token/
        secret/pass/admin — el masker vive en _cfg_public y cubre más que 'key')."""
        from conflux.web.server import _cfg_public
        out = _cfg_public({'debrid_key': 'K', 'nntp_pass': 'T',
                           'admin_token': 'A', 'stream_langs': ['es']})
        self.assertEqual(out['debrid_key'], '***')
        self.assertEqual(out['nntp_pass'], '***')
        self.assertEqual(out['admin_token'], '***')
        self.assertEqual(out['stream_langs'], ['es'])

    def test_no_secrets_en_logs(self):
        """Ningún módulo loggea el valor de claves/token del config."""
        for root, _, files in os.walk(SRC):
            for f in files:
                if not f.endswith('.py'):
                    continue
                code = _read(os.path.join(root, f))
                for m in re.finditer(r'(log_ev|_log\.\w+)\([^)]*', code):
                    self.assertNotRegex(m.group(0),
                                        r'CFG\[.(?:api|key|token|secret)',
                                        f'{f}: posible fuga de secreto en log')


class TestInputBounds(unittest.TestCase):
    def test_configure_clampa_body(self):
        code = _read(os.path.join(SRC, 'web', 'server.py'))
        self.assertIn('min(ln, 65536)', code)   # body POST capado a 64KB

    def test_fetch_cap_respuesta(self):
        code = _read(os.path.join(SRC, 'infra', 'net.py'))
        self.assertIn('6_000_000', code)        # respuestas >6MB descartadas


if __name__ == '__main__':
    unittest.main()
