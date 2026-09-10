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

"""Immutable Context path references."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

__all__ = ["Ref"]

_T = TypeVar("_T")


@dataclass(frozen=True, repr=False)
class Ref(Generic[_T]):
    """Immutable Context dependency handle for one declared dotted path."""

    path: str
    parts: tuple[str, ...] = field(init=False)

    @staticmethod
    def _split_path(path: str) -> tuple[str, ...]:
        if not isinstance(path, str):
            raise TypeError(f"Ref path must be str, got {type(path).__name__}.")
        if not path:
            raise ValueError("Ref path cannot be empty.")
        parts = tuple(path.split("."))
        if any(not part for part in parts):
            raise ValueError(f"Invalid Ref path: {path!r}.")
        return parts

    @staticmethod
    def _validate_name(name: Any, path: str) -> str:
        if not isinstance(name, str):
            raise TypeError(
                f"Invalid Schema key at {path or '<root>'}: expected str, "
                f"got {type(name).__name__}."
            )
        if not name or "." in name:
            raise ValueError(
                f"Invalid Schema key {name!r} at {path or '<root>'}; "
                "keys must be non-empty strings without dots."
            )
        return name

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", self._split_path(self.path))
