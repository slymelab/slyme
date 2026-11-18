"""Scope lookup defaults determine whether a missing result can be None."""

from __future__ import annotations

from typing_extensions import assert_type

from slyme.context import Identity, Scope, ScopeBinding


def check_types(scope: Scope, fallback: Scope | None) -> None:
    assert_type(Scope(), Scope)
    assert_type(Scope(label="child", parent=scope), Scope)
    assert_type(Scope(parent=scope), Scope)
    assert_type(scope.parent, Scope | None)
    Scope("child")  # type: ignore[misc]
    assert_type(Scope(parent=None), Scope)
    assert_type(scope.find("target"), Scope)
    assert_type(scope.find("target", scope), Scope)
    assert_type(scope.find("target", None), Scope | None)
    assert_type(scope.find("target", default=fallback), Scope | None)
    assert_type(scope.find_all("target"), tuple[Scope, ...])


def check_binding_types(binding: ScopeBinding) -> None:
    assert_type(ScopeBinding(Identity()), ScopeBinding)
    assert_type(ScopeBinding((Identity(), Identity())), ScopeBinding)
    assert_type(ScopeBinding([Identity(), Identity()]), ScopeBinding)
    assert_type(ScopeBinding(iter([Identity()])), ScopeBinding)
    assert_type(binding.identity, Identity)
    assert_type(binding.identities, tuple[Identity, ...])
    binding.identity = Identity()  # type: ignore[misc]
