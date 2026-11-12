"""mmdata - comparable market and macro data getter. Data only; analysis lives elsewhere."""
from .getter import FetchResult, fetch_series  # noqa: F401
from .coverage import apply_window, coverage_report  # noqa: F401

__version__ = "0.1.0"
