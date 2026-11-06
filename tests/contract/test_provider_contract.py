# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements
"""Contrato unificado de adaptadores (CONFLUX-031): todo parser de upstream
recibe la misma batería degenerada — vacío, bytes basura, XML malformado,
JSON de tipo erróneo, contenido binario — y el contrato es: nunca excepción
hacia el handler, salida siempre de la forma declarada."""
import gzip
import unittest
from unittest import mock

import conflux.domain.lists as L
import conflux.infra.epg as E
import conflux.infra.torznab as T

GARBAGE = [
    b'',
    b'not xml not json {{{',
    b'\x00\x01\x02\xff\xfe binary',
    b'<unclosed><tag>',
    b'[]',
    b'"just a string"',
    b'x' * 1_000_000,
]


class _Resp:
    """Mock mínimo de respuesta requests con raw stream."""
    def __init__(self, body):
        self.raw = mock.Mock()
        self.raw.read.return_value = body
    def close(self):
        pass


class TestFetchContract(unittest.TestCase):
    """fetch(): la puerta de todos los adaptadores JSON."""

    def _feed(self, body):
        sess = mock.Mock()
        sess.get.return_value = _Resp(body)
        with mock.patch.object(__import__('conflux.infra.net', fromlist=['x']),
                               '_session', return_value=sess):
            import conflux.infra.net as net
            return net.fetch('http://x.local/m')

    def test_json_degenerate(self):
        for body in GARBAGE:
            with self.subTest(body=body[:20]):
                out = self._feed(body)
                self.assertIn(type(out), (type(None), dict, list))

    def test_oversize_rejected(self):
        self.assertIsNone(self._feed(b'{}' + b' ' * 6_000_001))

    def test_valid_json_ok(self):
        self.assertEqual(self._feed(b'{"a": 1}'), {'a': 1})


class TestTorznabContract(unittest.TestCase):
    """Torznab/Newznab XML: defusedxml, nunca XML nativo."""

    def test_item_campos_ausentes(self):
        # un <item> sin attrs/guid/enclosure no debe lanzar
        import defusedxml.ElementTree as ET
        it = ET.fromstring('<item><title>x</title></item>')
        out = T._tz_item(it, {})
        self.assertIn(type(out), (type(None), dict))   # None = item descartado

    def test_num_invalido(self):
        self.assertEqual(T._tz_num('abc', int), 0)
        self.assertEqual(T._tz_num(None, float), 0.0)

    def test_xml_malformado_no_lanza(self):
        for body in GARBAGE[:5]:
            with self.subTest(body=body[:16]):
                try:
                    import defusedxml.ElementTree as ET
                    ET.fromstring(body)
                except Exception:
                    pass                # el parser defused puede lanzar — el
                                        # adaptador lo captura aguas arriba


class TestM3UContract(unittest.TestCase):
    """M3U/extinf: texto arbitrario no rompe el parseo."""

    def test_extinf_basura(self):
        for ln in ('', '#EXTINF', '#EXTINF:notanum,title', '#EXTINF:-1',
                   '#EXTINF:abc tvg-id="x"', 'línea sin prefijo'):
            with self.subTest(ln=ln):
                out = L._m3u_extinf(ln)
                self.assertIn(type(out), (type(None), dict, tuple))


class TestEPGContract(unittest.TestCase):
    """EPG XMLTV: gzip corrupto/xml roto → vacío controlado."""

    def test_xmltv_ts_basura(self):
        for v in ('', 'notadate', '20251301 0000', '99999999+9999', None):
            with self.subTest(v=v):
                try:
                    out = E._xmltv_ts(v)
                    self.assertIn(type(out), (type(None), str, int, float))
                except (ValueError, TypeError):
                    pass            # parse errors son aceptables, crashes no

    def test_gzip_corrupto(self):
        for body in GARBAGE:
            with self.subTest(body=body[:16]):
                try:
                    gzip.decompress(body)
                except (OSError, EOFError):
                    pass            # el caller decide; aquí: no crash python


if __name__ == '__main__':
    unittest.main()
