"""Autonomous stock & cryptocurrency investment research engine.

This package is *decision-support software*. It produces evidence-based
research output; it never places trades and it never fabricates data.

Layering (each layer is importable and testable on its own):

    ingestion -> quality -> storage -> features -> analysis -> pipeline

Nothing below ``analysis`` is allowed to import from ``pipeline``; nothing in
``features``/``analysis`` may perform network I/O.
"""

__version__ = "0.1.0"

DISCLAIMER = (
    "Research and decision support only. Not investment advice. Model output is "
    "uncertain, backtests are simulations, and past performance does not guarantee "
    "future results. Recommendations can be wrong; consider your own risk tolerance "
    "and financial circumstances."
)
