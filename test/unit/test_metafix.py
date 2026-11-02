"""Unit tests conflux.domain.metafix — fix de metas."""
import unittest
from unittest.mock import patch

import conflux.domain.metafix as mf


class TestFixMetas(unittest.TestCase):
    def test_yt_id_desde_id(self):
        d = {'meta': {'id': 'yt_id:UCabc'}}
        with patch.object(mf, '_fix_meta_basic') as b, \
             patch.object(mf, '_fix_meta_deep'):
            mf._fix_metas(d)
            b.assert_called_once()

    def test_sin_yt_salta(self):
        d = {'meta': {'id': 'tt123'}}
        with patch.object(mf, '_fix_meta_basic') as b:
            mf._fix_metas(d)
            b.assert_not_called()

    def test_metas_lista(self):
        d = {'metas': [{'id': 'yt_id:A'}, {'id': 'yt_id:B'}]}
        with patch.object(mf, '_fix_meta_basic') as b:
            mf._fix_metas(d)
            self.assertEqual(b.call_count, 2)


if __name__ == '__main__':
    unittest.main()
