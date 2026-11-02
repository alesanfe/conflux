# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.app.bg — prefetch + refresher."""
import unittest
from unittest.mock import patch

from conflux.app import bg


class TestPrefetch(unittest.TestCase):
    def test_no_reentra_si_ya_corre(self):
        bg._prefetching.set()
        try:
            with patch.object(bg, '_POOL_BG') as p:
                bg._prefetch_catalogs()
                p.submit.assert_not_called()
        finally:
            bg._prefetching.clear()


if __name__ == '__main__':
    unittest.main()
