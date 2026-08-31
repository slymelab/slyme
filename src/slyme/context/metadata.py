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

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "ARG",
    "Arg",
    "HELP",
    "OUTPUT",
    "TYPE",
]

# Metadata Key
ARG = "node.arg"
HELP = "node.help"
TYPE = "node.type"
OUTPUT = "node.output"

_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(frozen=True)
class Arg:
    """
    Argument definition to be used in Ref metadata.
    Designed to be minimal and universal, compatible with argparse, hydra, etc.

    Example:
        Ref("model.lr", metadata={ARG: Arg(default=1e-3, help="Learning rate")})
    """

    default: Any = _MISSING
    default_factory: Callable[[], Any] | _Missing = _MISSING
    help: str | None = None
    type: type | None = None
    choices: Iterable[Any] | None = None
    required: bool = False
    nargs: str | int | None = None
    aliases: list[str] = field(default_factory=list)
    metavar: str | None = None

    @staticmethod
    def is_missing(value: Any) -> bool:
        return value is _MISSING

    def resolve_default(self) -> Any:
        if self.default is not _MISSING:
            return self.default
        if callable(self.default_factory):
            return self.default_factory()
        return _MISSING
