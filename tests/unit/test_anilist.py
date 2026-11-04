# pylint: disable=missing-docstring,protected-access,import-outside-toplevel
"""Unit tests conflux.infra.anilist — row parsing."""
import unittest

import conflux.infra.anilist as A


class TestAnilistRow(unittest.TestCase):
    def test_row_sin_mal_none(self):
        self.assertIsNone(A._anilist_row({'media': {'idMal': None}}))

    def test_row_ok(self):
        a = {'episode': 5, 'airingAt': 100,
             'media': {'idMal': 123, 'coverImage': {'large': 'u'},
                        'title': {'english': 'X'}}}
        r = A._anilist_row(a)
        self.assertEqual(r['ep'], 5)
        self.assertEqual(r['idMal'], 123)
        self.assertEqual(r['title'], 'X')


if __name__ == '__main__':
    unittest.main()


class TestAiringLoop(unittest.TestCase):
    """_anilist_airing: páginas hasta vacío, dedup (idMal,ep), row None fuera."""

    def test_paginas_dedup_y_corte(self):
        import unittest.mock as um
        rows1 = [
            {'episode': 1, 'airingAt': 1, 'media': {'idMal': 10}},
            {'episode': 1, 'airingAt': 1, 'media': {'idMal': 10}},   # dup
            {'episode': 2, 'airingAt': 1, 'media': {}},              # sin mal
            {'episode': 3, 'airingAt': 1, 'media': {'idMal': 11}},
        ]
        with um.patch.object(A, '_anilist_page',
                             side_effect=[rows1, []]) as pg:
            out = A._anilist_airing(7)
        self.assertEqual(len(out), 2)
        self.assertEqual([r['idMal'] for r in out], [10, 11])
        self.assertEqual(pg.call_count, 2)              # corta en página vacía

    def test_t_end_dias_y_cap_3_paginas(self):
        import unittest.mock as um
        import time
        fila = [{'episode': 1, 'airingAt': 1, 'media': {'idMal': 10}}]
        with um.patch.object(A, '_anilist_page', return_value=fila) as pg:
            A._anilist_airing(7)
        self.assertEqual(pg.call_count, 3)              # tope: páginas 1,2,3
        t_end = pg.call_args_list[0][0][1]
        esperado = int(time.time()) + 7 * 86400
        self.assertAlmostEqual(t_end, esperado, delta=5)  # days*86400 real

    def test_page_vacia_termina(self):
        import unittest.mock as um
        with um.patch.object(A, '_anilist_page', return_value=[]) as pg:
            self.assertEqual(A._anilist_airing(7), [])
        pg.assert_called_once()


class TestAnilistPage(unittest.TestCase):
    def _sess(self, payload=None, exc=None):
        import unittest.mock as um
        s = um.MagicMock()
        if exc:
            s.post.side_effect = exc
        else:
            s.post.return_value.json.return_value = payload
        return s

    def test_page_devuelve_schedules(self):
        import unittest.mock as um
        payload = {'data': {'Page': {'airingSchedules': [{'episode': 1}]}}}
        with um.patch.object(A, '_session', return_value=self._sess(payload)):
            out = A._anilist_page(1, 999)
        self.assertEqual(out, [{'episode': 1}])

    def test_page_error_vacio(self):
        import unittest.mock as um
        import requests
        with um.patch.object(A, '_session',
                             return_value=self._sess(exc=requests.ConnectionError())):
            self.assertEqual(A._anilist_page(1, 999), [])

    def test_page_sin_data_vacio(self):
        import unittest.mock as um
        with um.patch.object(A, '_session', return_value=self._sess({})):
            self.assertEqual(A._anilist_page(1, 999), [])
