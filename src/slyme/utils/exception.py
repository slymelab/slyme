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

import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import Union


@contextmanager
def enrich_exception(
    info: str,
    exc_types: Union[type[Exception], tuple[type[Exception], ...]] = Exception,
) -> Generator[None, None, None]:
    """
    Context manager to enrich exceptions with context info.
    """
    try:
        yield
    except exc_types as e:
        # Strategy 1: Modern Python (Preferred)
        if sys.version_info >= (3, 11):
            e.add_note(info)
            raise

        # Strategy 2: Legacy / Compatibility
        # Construct the new message
        if len(e.args) > 0 and isinstance(e.args[0], str):
            e.args = (f"{e.args[0]} ({info})", *e.args[1:])
        else:
            e.args = (*e.args, f"({info})")
        raise
