# Copyright 2026 The SlymeLab Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
