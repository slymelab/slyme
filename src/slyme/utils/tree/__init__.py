"""Compatibility imports; removed in 0.3.0. Use ``slyme.tree`` instead."""

from __future__ import annotations

import warnings

from slyme.tree import *  # noqa: F403
from slyme.tree import __all__ as __all__

warnings.warn(
    "slyme.utils.tree is deprecated and will be removed in 0.3.0; use slyme.tree instead.",
    DeprecationWarning,
    stacklevel=2,
)
