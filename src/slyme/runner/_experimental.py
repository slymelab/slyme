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

"""Shared experimental notice for the runner prototype surfaces."""

from slyme.utils.warning import warning_once


EXPERIMENTAL_RUNNER_WARNING = (
    "slyme.runner, slyme.cli.command, and the slyme command-line entry point "
    "are experimental prototypes; their APIs, commands, and wire formats may "
    "change incompatibly in future releases."
)


def warn_experimental_runner(*, stacklevel: int = 1) -> None:
    """Warn once that the runner and its command-line surfaces are prototypes."""
    warning_once(
        EXPERIMENTAL_RUNNER_WARNING,
        FutureWarning,
        stacklevel=stacklevel + 1,
    )
