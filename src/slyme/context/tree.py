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

from types import MappingProxyType

from slyme.utils.pytree import (
    PYTREE_ENGINE_REGISTRY,
    PyTreeEngine,
)
from slyme.utils.pytree.common import flatten_mapping_proxy, unflatten_mapping_proxy

CTX_EVAL_ENGINE = PyTreeEngine("ctx_eval_engine", register_defaults=True)
PYTREE_ENGINE_REGISTRY.register(CTX_EVAL_ENGINE, key="ctx_eval_engine")
CTX_EVAL_ENGINE.register(
    MappingProxyType, flatten_mapping_proxy, unflatten_mapping_proxy
)
