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

"""Experimental runner prototype.

The public API and wire format may receive breaking changes before this
package is stabilized.
"""

from ._experimental import warn_experimental_runner as _warn_experimental_runner

_warn_experimental_runner(stacklevel=2)

from .io import Captured, capture_stdio, emit_envelope
from .protocol import (
    KIND_CALL,
    KIND_DISCOVER,
    KIND_INFO,
    KIND_NODES,
    PROTOCOL,
    SCHEMA_VERSION,
    error_payload,
    make_envelope,
    protocol_id,
)

__all__ = [
    "Captured",
    "KIND_CALL",
    "KIND_DISCOVER",
    "KIND_INFO",
    "KIND_NODES",
    "PROTOCOL",
    "SCHEMA_VERSION",
    "capture_stdio",
    "emit_envelope",
    "error_payload",
    "make_envelope",
    "protocol_id",
]
