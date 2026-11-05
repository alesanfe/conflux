"""apply_env.py — vuelca .env a config.json/sources.json (tipado, sources
extra, guard SSRF de M3U_URL, escrituras atomicas)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
import apply_env  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = apply_env.ROOT
        apply_env.ROOT = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, apply_env, 'ROOT', self.old_root)
        self.cfg_path = os.path.join(self.tmp.name, 'config.json')

    def _env(self, text):
        p = os.path.join(self.tmp.name, '.env')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        return p

    def _main(self, env_path):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['apply_env.py', env_path]), \
                redirect_stdout(out):
            rc = apply_env.main()
        return rc, out.getvalue()

    def _cfg(self):
        with open(self.cfg_path, encoding='utf-8') as f:
            return json.load(f)


class TestReadEnv(Base):
    def test_parse_basico(self):
        p = self._env('# comentario\n\nTRAKT_CLIENT_ID=abc\nSIN_IGUAL\n'
                      'QUOTED="con espacios"\nlower=mix\n')
        env = apply_env._read_env(p)
        self.assertEqual(env['TRAKT_CLIENT_ID'], 'abc')
        self.assertEqual(env['QUOTED'], 'con espacios')
        self.assertNotIn('SIN_IGUAL', env)
        self.assertIn('LOWER', env)                  # k.strip().upper()


class TestMain(Base):
    def test_sin_env_no_escribe(self):
        rc, out = self._main(os.path.join(self.tmp.name, 'no.env'))
        self.assertEqual(rc, 0)
        self.assertIn('sin', out)
        self.assertFalse(os.path.exists(self.cfg_path))

    def test_tipos(self):
        p = self._env('STREAM_LANGS=es, en ,x\nCALENDAR_MONTHS_AHEAD=4\n'
                      'PARENTAL=true\nTORRENT_HEALTH=0\nRPDB_KEY=k1\n')
        rc, _ = self._main(p)
        cfg = self._cfg()
        self.assertEqual(rc, 0)
        self.assertEqual(cfg['stream_langs'], ['es', 'en', 'x'])
        self.assertEqual(cfg['calendar_months_ahead'], 4)
        self.assertIs(cfg['parental'], True)
        self.assertIs(cfg['torrent_health'], False)
        self.assertEqual(cfg['rpdb_key'], 'k1')

    def test_vacio_no_pisa(self):
        """v == '' salta la clave (Eq@124: '' se colaría como valor)."""
        p = self._env('RPDB_KEY=\nTRAKT_CLIENT_ID=t\n')
        self._main(p)
        cfg = self._cfg()
        self.assertNotIn('rpdb_key', cfg)
        self.assertEqual(cfg['trakt_client_id'], 't')

    def test_clave_desconocida_omitida(self):
        p = self._env('UNKNOWN_ENV_KEY=x\n')
        self._main(p)
        self.assertNotIn('UNKNOWN_ENV_KEY', json.dumps(self._cfg()))

    def test_merge_no_destruye(self):
        with open(self.cfg_path, 'w', encoding='utf-8') as f:
            json.dump({'otra': 'vieja', 'rpdb_key': 'old'}, f)
        p = self._env('RPDB_KEY=new\n')
        self._main(p)
        self.assertEqual(self._cfg(), {'otra': 'vieja', 'rpdb_key': 'new'})


class TestSourcesExtra(Base):
    def test_append_y_update_case_insensitive(self):
        src_path = os.path.join(self.tmp.name, 'sources.json')
        with open(src_path, 'w', encoding='utf-8') as f:
            json.dump([{'name': 'Existente', 'base': 'http://old'}], f)
        p = self._env('SOURCES_EXTRA=existente|http://new;Nuevo|http://n2;'
                      'sinbarra;|vacio\nRPDB_KEY=k\n')
        self._main(p)
        with open(src_path, encoding='utf-8') as f:
            srcs = json.load(f)
        self.assertEqual(srcs, [{'name': 'Existente', 'base': 'http://new'},
                                {'name': 'Nuevo', 'base': 'http://n2'}])

    def test_sin_extra_no_toca_sources(self):
        p = self._env('RPDB_KEY=k\n')
        self._main(p)
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp.name, 'sources.json')))


    def test_argv_sin_args_usa_env_root(self):
        """len(sys.argv) > 1 — argv solo con argv[0] lee ROOT/.env
        (mutante Gt@117: >= indexaría argv[1] -> IndexError)."""
        self._env('RPDB_KEY=pordefecto\n')
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['apply_env.py']), \
                redirect_stdout(out):
            rc = apply_env.main()
        self.assertEqual(rc, 0)
        self.assertEqual(self._cfg()['rpdb_key'], 'pordefecto')


def _touch(url, dst):
    """urlretrieve falso — escribe el .m3u y cierra el handle."""
    with open(dst, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U')
    return dst, None


class TestM3u(Base):
    def test_url_privada_rechazada(self):
        p = self._env('M3U_URL=ftp://x/l.m3u\n')
        with mock.patch.object(apply_env, '_public_url', return_value=False), \
                mock.patch('urllib.request.urlretrieve') as dl:
            rc, out = self._main(p)
        self.assertIn('rechazada', out)
        dl.assert_not_called()
        self.assertFalse(os.path.isdir(os.path.join(self.tmp.name, 'm3u')))

    def test_url_ok_descarga_y_apunta(self):
        p = self._env('M3U_URL=https://h/list.m3u\n')
        dst = os.path.join(self.tmp.name, 'm3u', 'list.m3u')
        def fake_retrieve(url, dst_):
            with open(dst_, 'w') as f:
                f.write('#EXTM3U')
            return dst_, None
        with mock.patch.object(apply_env, '_public_url', return_value=True), \
                mock.patch('urllib.request.urlretrieve', fake_retrieve):
            rc, out = self._main(p)
        cfg = self._cfg()
        self.assertEqual(cfg['m3u_path'], dst)
        self.assertTrue(os.path.exists(dst))

    def test_m3u_dir_ya_existe(self):
        """makedirs(exist_ok=True) — dir preexistente no rompe la descarga
        (mutante True@167: exist_ok=False -> FileExistsError)."""
        os.makedirs(os.path.join(self.tmp.name, 'm3u'))
        p = self._env('M3U_URL=https://h/l.m3u\n')
        with mock.patch.object(apply_env, '_public_url', return_value=True), \
                mock.patch('urllib.request.urlretrieve',
                           _touch):
            rc, out = self._main(p)
        self.assertNotIn('fallida', out)
        self.assertIn('m3u_path', json.dumps(self._cfg()))

    def test_url_sin_nombre_usa_list_m3u(self):
        """basename '' -> 'list.m3u' (mutante Or@168: dst sería el propio
        dir m3u/ -> IsADirectoryError -> 'descarga fallida')."""
        p = self._env('M3U_URL=https://h/\n')
        dst = os.path.join(self.tmp.name, 'm3u', 'list.m3u')
        with mock.patch.object(apply_env, '_public_url', return_value=True), \
                mock.patch('urllib.request.urlretrieve',
                           _touch):
            self._main(p)
        self.assertEqual(self._cfg()['m3u_path'], dst)
        self.assertTrue(os.path.isfile(dst))

    def test_public_url_guard(self):
        """La copia local del guard SSRF: scheme http(s) + no link-local/
        multicast/reserved/unspecified (RFC1918 y loopback SÍ: LAN legit)."""
        pu = apply_env._public_url
        self.assertFalse(pu('ftp://x/y'))
        self.assertFalse(pu('http://169.254.169.254/x'))
        self.assertFalse(pu(''))
        self.assertTrue(pu('http://127.0.0.1:9700/l.m3u'))
        self.assertTrue(pu('http://192.168.1.10/l.m3u'))

    def test_scheme_malo_aunque_el_host_resuelva(self):
        """scheme no-http(s) corta ANTES de DNS (mutante Or@30: con host
        resoluble pasaría a getaddrinfo y daría True). IPv4 literal —
        sin DNS externo."""
        self.assertFalse(apply_env._public_url('ftp://127.0.0.1/x.m3u'))
        self.assertFalse(apply_env._public_url('javascript://127.0.0.1/x'))

    def test_puerto_default_scheme(self):
        """443 para https, 80 para http (mutantes 443+1@32/Eq@32)."""
        ports = []
        def fake_gai(host, port, proto=0):
            ports.append(port)
            return [(2, 1, 6, '', ('8.8.8.8', port))]
        with mock.patch('socket.getaddrinfo', fake_gai):
            apply_env._public_url('https://h.example/x')
            apply_env._public_url('http://h.example/x')
        self.assertEqual(ports, [443, 80])

    def test_puerto_explicito_gana(self):
        """p.port or default — puerto en la URL manda (mutante Or@32:
        `and` devolvería el default 443 con puerto explícito)."""
        ports = []
        def fake_gai(host, port, proto=0):
            ports.append(port)
            return [(2, 1, 6, '', ('8.8.8.8', port))]
        with mock.patch('socket.getaddrinfo', fake_gai):
            apply_env._public_url('https://h.example:8443/x')
        self.assertEqual(ports, [8443])

    def test_error_dns_es_false(self):
        """getaddrinfo falla -> False (mutante False@38 lo haría True)."""
        with mock.patch('socket.getaddrinfo', side_effect=OSError('dns')):
            self.assertFalse(apply_env._public_url('https://h.example/x'))


class TestAtomicJson(Base):
    def test_escribe_json_valido(self):
        p = os.path.join(self.tmp.name, 'x.json')
        apply_env._atomic_json(p, {'a': 1})
        with open(p, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'a': 1})

    def test_utf8_sin_escapar(self):
        """ensure_ascii=False — no-ASCII se escribe literal (mutante
        False@88 lo escaparía a \\uXXXX)."""
        p = os.path.join(self.tmp.name, 'x.json')
        apply_env._atomic_json(p, {'k': 'áéí'})
        with open(p, encoding='utf-8') as f:
            self.assertIn('áéí', f.read())

    def test_error_limpia_tmp(self):
        p = os.path.join(self.tmp.name, 'x.json')
        with mock.patch('json.dump', side_effect=TypeError('boom')):
            with self.assertRaises(TypeError):
                apply_env._atomic_json(p, {'a': 1})
        self.assertFalse(os.path.exists(p))
        self.assertEqual([x for x in os.listdir(self.tmp.name)
                          if x.endswith('.tmp')], [])


if __name__ == '__main__':
    unittest.main()
