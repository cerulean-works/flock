import numpy as np
import pytest

from app.api.src.core.history import History, Series


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


@pytest.mark.parametrize("x_key", ["_step", "_runtime"])
@pytest.mark.parametrize("sparse", [False, True])
def test_curve_with_axis_newer_than_run_line_count(x_key, sparse):
    hist = History()
    hist.append_rows(0, [{x_key: 0, "loss": 1.0}, {} if sparse else {x_key: 1}, {x_key: 2}])
    # Run metadata was read before another request appended the newer chunks.
    hist.n_rows = 1

    curve = hist.curve("loss", x_key)
    assert curve is not None
    x, y = curve
    np.testing.assert_array_equal(x, [0])
    np.testing.assert_array_equal(y, [1.0])
    rows, x, y = hist.delta(0, x_key)["loss"]
    np.testing.assert_array_equal(rows, [0])
    np.testing.assert_array_equal(x, [0])
    np.testing.assert_array_equal(y, [1.0])


def test_x_at_missing_rows_after_dense_axis():
    hist = History()
    hist.append_rows(0, [{"_step": 0}, {"_step": 1}])
    # A requested row beyond the dense axis should be missing, like a sparse-axis gap.
    np.testing.assert_array_equal(hist.x_at("_step", np.array([0, 3])), [0, np.nan])


@pytest.mark.parametrize("axis_rows", [[], [1, 4], [0, 1, 2, 3, 4]])
def test_x_at_matches_only_logged_rows(axis_rows):
    hist = History(n_rows=5)
    axis = Series()
    axis.extend(axis_rows, [row * 0.5 for row in axis_rows])
    hist.series["axis"] = axis
    rows = np.arange(6, dtype=np.int32)
    expected = [row * 0.5 if row in axis_rows else np.nan for row in rows]
    np.testing.assert_array_equal(hist.x_at("axis", rows), expected)
    assert hist.x_at("axis", rows[:0]).size == 0
