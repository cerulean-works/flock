import numpy as np
import pytest

from app.api.src.core.history import Series


@pytest.mark.parametrize(
    ("rows", "dense"),
    [
        (list(range(500)), True),  # logged on every line: rows are implied
        ([0, 3, 6, 9], False),  # logged every 3rd line
        (list(range(5, 15)), False),  # contiguous, but not from line 0
        ([0], True),
        ([7], False),
        ([], True),
    ],
)
def test_series_blob_round_trip(rows, dense):
    vals = [float(r) * 0.5 - 1 for r in rows]
    original = Series()
    original.extend(rows, vals)
    blob = original.encode()
    assert len(blob) == len(rows) * (8 if dense else 12)
    decoded = Series.decode(blob, len(rows))
    np.testing.assert_array_equal(decoded.rows, np.asarray(rows, dtype=np.int32))
    np.testing.assert_array_equal(decoded.vals, np.asarray(vals))
    decoded.extend([10_000], [1.0])  # decoded blobs are read-only views; appending must copy
    assert decoded.n == len(rows) + 1
