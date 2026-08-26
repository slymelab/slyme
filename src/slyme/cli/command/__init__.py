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

"""Experimental subcommands for the ``slyme`` runner CLI.

Each module exposes ``register(subparsers)`` (adds its subparser and sets its
``handler``) and ``run(args) -> int``.

This package is a prototype and its commands may receive breaking changes
before stabilization.
"""

from slyme.runner._experimental import (
    warn_experimental_runner as _warn_experimental_runner,
)

_warn_experimental_runner(stacklevel=2)

from . import call, discover, info, nodes

__all__ = ["call", "discover", "info", "nodes"]
