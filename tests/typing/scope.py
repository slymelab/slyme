"""Scope lookup defaults determine whether a missing result can be None."""

from __future__ import annotations

from typing_extensions import assert_type

from slyme.context import Scope


def check_types(scope: Scope, fallback: Scope | None) -> None:
    assert_type(Scope(), Scope)
    assert_type(Scope(label="child", parents=scope), Scope)
    assert_type(Scope(parents=(scope,)), Scope)
    assert_type(scope.parents, tuple[Scope, ...])
    Scope("child")  # type: ignore[misc]
    Scope(parents=None)  # type: ignore[arg-type]
    assert_type(scope.find("target"), Scope)
    assert_type(scope.find("target", scope), Scope)
    assert_type(scope.find("target", None), Scope | None)
    assert_type(scope.find("target", default=fallback), Scope | None)
    assert_type(scope.find_all("target"), tuple[Scope, ...])
