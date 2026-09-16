"""JSON-safe conversion for the pandas DataFrames/Series felinni's analysis
functions return: Timestamps -> ISO strings, NaN -> None, index -> a column."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _clean(value):
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if math.isnan(value) else float(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_clean(v) for v in value]
    return value


def records(df: pd.DataFrame | pd.Series) -> list[dict]:
    """DataFrame/Series (possibly with a non-trivial index) -> list of plain dicts."""
    if isinstance(df, pd.Series):
        df = df.to_frame(name=df.name or "value")
    if df.index.name or not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    return [{col: _clean(val) for col, val in row.items()} for row in df.to_dict(orient="records")]
