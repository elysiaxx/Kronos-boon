"""
Guard the HTML reports' embedded data payload.

Both reports ship their numbers as JSON inside the page and parse them in the
browser's first statement. NaN and Infinity are not JSON: Python writes them
as bare tokens and reads them back happily, while every browser's JSON.parse
rejects them - so the page dies before drawing anything and shows only its
server-rendered shell. Statistics go non-finite in ordinary states (no losing
trade yet, fewer than two trades), which are exactly the early runs the report
is most useful for, so this is tested rather than assumed.

Run directly - no pytest needed:

    python -m tests.test_report_payload
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_backtest.report_assets import embed_json, finite_only  # noqa: E402

#: json.loads accepts NaN; browsers do not. Mirror the strict behaviour here.
_STRICT = json.JSONDecoder(parse_constant=lambda c: (_ for _ in ()).throw(
    ValueError(f"non-JSON constant in payload: {c}")))


def _parse_strict(text: str):
    return _STRICT.decode(text)


def test_non_finite_values_become_null():
    inf, nan = float("inf"), float("nan")
    data = {"a": nan, "b": inf, "c": -inf, "d": 1.5,
            "nested": {"e": nan, "list": [1.0, nan, inf, 2.0]},
            "tuple": (nan, 3.0)}
    out = _parse_strict(embed_json(data))
    assert out["a"] is None and out["b"] is None and out["c"] is None
    assert out["d"] == 1.5
    assert out["nested"]["e"] is None
    assert out["nested"]["list"] == [1.0, None, None, 2.0]
    assert out["tuple"] == [None, 3.0]
    print("  ok: NaN and +/-Infinity become null at every depth")


def test_payload_has_no_bare_non_json_tokens():
    text = embed_json({"x": float("nan"), "y": [float("inf")]})
    assert not re.search(r":\s*(NaN|Infinity|-Infinity)", text), text
    print("  ok: the serialized payload carries no bare NaN/Infinity token")


def test_all_winning_trades_still_renders():
    """The real failure: profit factor and avg loss are NaN until a trade loses."""
    stats = {"n_trades": 5, "wins": 5, "losses": 0,
             "avg_loss": float("nan"), "profit_factor": float("nan"),
             "per_trade": {"mean": 1.4, "ci_low": float("nan"),
                           "ci_high": float("nan"), "p_value": float("nan")}}
    out = _parse_strict(embed_json({"stats": stats}))
    assert out["stats"]["profit_factor"] is None
    assert out["stats"]["per_trade"]["p_value"] is None
    assert out["stats"]["wins"] == 5
    print("  ok: an all-winners session still produces parseable JSON")


def test_script_tag_cannot_be_closed_early():
    text = embed_json({"note": "</script><script>alert(1)</script>"})
    assert "</script>" not in text, text
    assert _parse_strict(text)["note"].endswith("</script>")
    print("  ok: a payload string cannot close the script block")


def test_numpy_non_finite_is_scrubbed():
    try:
        import numpy as np
    except ImportError:
        print("  skip: numpy not installed")
        return
    data = {"arr": np.array([1.0, np.nan, np.inf]), "scalar": np.float64("nan")}
    out = _parse_strict(embed_json(data))
    assert out["arr"] == [1.0, None, None], out["arr"]
    assert out["scalar"] is None
    print("  ok: numpy arrays and scalars are scrubbed too")


def test_finite_only_leaves_normal_data_alone():
    data = {"i": 3, "s": "x", "b": True, "n": None, "f": 2.5, "l": [1, "a", None]}
    assert finite_only(data) == data
    print("  ok: ordinary values pass through unchanged")


TESTS = [test_non_finite_values_become_null,
         test_payload_has_no_bare_non_json_tokens,
         test_all_winning_trades_still_renders,
         test_script_tag_cannot_be_closed_early,
         test_numpy_non_finite_is_scrubbed,
         test_finite_only_leaves_normal_data_alone]

if __name__ == "__main__":
    for t in TESTS:
        print(t.__name__)
        t()
    print(f"\n{len(TESTS)} payload tests passed")
