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

from enum import Enum
from typing import Any, Optional, Union
from dataclasses import dataclass, field
from collections.abc import Iterable, Callable

__all__ = [
    "ARG",
    "Arg",
    "HELP",
    "TYPE",
]

# Metadata Key
ARG = "node.arg"
HELP = "node.help"
TYPE = "node.type"

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
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING
    help: Optional[str] = None
    type: Optional[type] = None
    choices: Optional[Iterable[Any]] = None
    required: bool = False
    nargs: Union[str, int, None] = None
    aliases: list[str] = field(default_factory=list)
    metavar: Optional[str] = None

    @staticmethod
    def is_missing(value: Any) -> bool:
        return value is _MISSING

    def resolve_default(self) -> Any:
        if self.default is not _MISSING:
            return self.default
        if self.default_factory is not _MISSING:
            return self.default_factory()
        return _MISSING
