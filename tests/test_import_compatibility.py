from __future__ import annotations

import importlib
import pickle
import subprocess
import sys

import pytest

pytestmark = pytest.mark.filterwarnings(
    r"ignore:slyme.utils.(tree|execution) is deprecated:DeprecationWarning"
)


@pytest.mark.parametrize("name", ["execution", "tree", "tree.core", "tree.common"])
def test_old_imports_reexport_the_canonical_objects(name: str) -> None:
    canonical = importlib.import_module(f"slyme.{name}")
    legacy = importlib.import_module(f"slyme.utils.{name}")
    names = getattr(
        canonical, "__all__", [n for n in vars(canonical) if not n.startswith("_")]
    )
    for symbol in names:
        assert getattr(legacy, symbol) is getattr(canonical, symbol)


def test_legacy_pickle_globals_keep_resolving() -> None:
    from slyme.execution import FlatExec
    from slyme.tree import TreeHandler

    assert pickle.loads(b"cslyme.utils.execution\nFlatExec\n.") is FlatExec
    assert pickle.loads(b"cslyme.utils.tree.core\nTreeHandler\n.") is TreeHandler


def test_only_legacy_tree_and_execution_imports_warn() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import warnings
with warnings.catch_warnings(record=True) as seen:
    warnings.simplefilter("always")
    import slyme.tree, slyme.execution, slyme.context, slyme.node
    import slyme.utils, slyme.utils.exception, slyme.utils.warning
    assert not seen, seen
    import slyme.utils.tree.core, slyme.utils.tree.common
    import slyme.utils.execution
    assert len(seen) == 2, seen
    for warning in seen:
        assert warning.category is DeprecationWarning
        assert "0.3.0" in str(warning.message)
    assert "slyme.utils.tree" in str(seen[0].message)
    assert "slyme.utils.execution" in str(seen[1].message)
""",
        ],
        check=True,
    )
