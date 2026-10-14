"""Compatibility imports; removed in 0.3.0. Use ``slyme.execution`` instead."""

from __future__ import annotations

import warnings

from slyme.execution import *  # noqa: F403
from slyme.execution import __all__ as __all__

warnings.warn(
    "slyme.utils.execution is deprecated and will be removed in 0.3.0; "
    "use slyme.execution instead.",
    DeprecationWarning,
    stacklevel=2,
)
