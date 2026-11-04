# pylint: disable=missing-docstring
"""Estrategias hypothesis compartidas por los ficheros test_prop_*.

Separadas para que cada fichero sea un shard independiente de --jobs:
un import por worker, estrategias identicas en todos."""
import os

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st

_MAX = settings(max_examples=int(os.getenv('HYP_EXAMPLES', '60')),
               deadline=None, suppress_health_check=list(HealthCheck))

_ANY_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() |
    st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=50),
    lambda ch: st.lists(ch, max_size=4) |
    st.dictionaries(st.text(max_size=12), ch, max_size=4),
    max_leaves=25)

_STREAM_DICT = st.dictionaries(
    st.sampled_from(['infoHash', 'url', 'fileIdx', 'title', 'name', 'sources',
                     'behaviorHints', 'seeders', 'size', 'description']),
    _ANY_JSON, max_size=8)
