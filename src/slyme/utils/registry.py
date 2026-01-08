"""
A convenient registry util that dynamically retrieves items based on keys.
"""

from .collection import MutableMappingProxy
from .decorator import auto_decorator
from .typing import (
    Union,
    TypeVar,
    overload,
    Callable,
)
from .inspect import resolve_mro
from .constant import MISSING, Missing

_T = TypeVar("_T")
_KT = TypeVar("_KT")
_VT = TypeVar("_VT")
_VT2 = TypeVar("_VT2")


class GeneralRegistry(MutableMappingProxy[_KT, _VT]):
    """
    A general registry whose type of keys can be any specified value.

    WARNING: You should avoid instantiating ``GeneralRegistry`` in any main
    scripts. It should be created in other non-main modules and imported by
    the main scripts instead.
    """

    def __init__(
        self,
        namespace: Union[str, Missing] = MISSING,
        /,
        *,
        strict: bool = True,
    ):
        super().__init__()
        self.namespace = repr(self) if namespace is MISSING else namespace
        self.strict = strict

    def _resolve_strict(self, strict: Union[bool, Missing] = MISSING):
        """
        Parse the given ``strict`` value. If ``strict`` is ``MISSING``, then
        return ``self.strict`` (i.e., the config of the registry), else
        directly return ``strict`` (which will override the registry config
        when registering a specific item).
        """
        return strict if strict is not MISSING else self.strict

    @overload
    def __call__(
        self,
        obj: Missing = MISSING,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> Callable[[_VT2], _VT2]: ...
    @overload
    def __call__(
        self,
        obj: _VT2,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2: ...
    @auto_decorator(index=1, keyword="obj")
    def __call__(
        self,
        obj: Union[_VT2, Missing] = MISSING,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2:
        """
        Register an item. Can be used as a decorator or a normal method.
        """

        def decorator(_obj: _VT2) -> _VT2:
            # Call the core register method.
            self._register(_obj, key, strict)
            return _obj

        return decorator

    def _register(
        self, obj: _VT, key: Union[_KT, Missing], strict: Union[bool, Missing]
    ) -> None:
        """
        Core register method. Can be overridden by subclasses for extended features.
        """
        strict = self._resolve_strict(strict)
        if key is MISSING:
            # The key should be explicitly specified or be properly handled by subclasses,
            # so it should never be ``MISSING`` here.
            raise ValueError(
                f"Error when registering ``{repr(obj)}`` in registry ``{self.namespace}``. "
                f"Key cannot be ``MISSING``. Check the key setting."
            )
        if key in self and strict:
            raise ValueError(
                f"Key ``{key}`` already exists in registry ``{self.namespace}``."
            )
        # Register ``obj`` with ``key``.
        self[key] = obj


class Registry(GeneralRegistry[str, _VT]):
    """
    A more commonly used registry with key type set to str.
    """

    def _register(
        self, obj: _VT, key: Union[str, Missing], strict: Union[bool, Missing]
    ) -> None:
        """
        Core register method. If ``key`` is not specified, get the ``__name__`` of
        ``obj`` as ``key``.
        """
        if key is MISSING:
            # Try to get the ``__name__`` of ``obj`` if ``key`` is not specified.
            key = getattr(obj, "__name__", MISSING)
        if key is MISSING:
            raise ValueError(
                f"Registry cannot correctly infer the ``key`` when registering "
                f"``{repr(obj)}`` in registry {self.namespace}. Neither is the ``key`` "
                f"param specified, nor does the attribute ``__name__`` exist in "
                f"``{repr(obj)}``."
            )
        return super()._register(obj, key, strict)


class TypeRegistry(GeneralRegistry[type[_KT], _VT]):
    """
    Registry that uses Python types as keys and supports MRO-based lookup.
    """

    def lookup_cls(self, key: type[_KT]) -> Union[type[_KT], None]:
        """
        Walk through the MRO of the given ``key`` (a class) and return the first
        base class (KEY) that is registered in this registry.
        """
        for base in resolve_mro(key):
            if base in self:
                return base
        return None

    @overload
    def lookup(self, key: type[_KT], default: Missing = MISSING) -> _VT: ...
    @overload
    def lookup(self, key: type[_KT], default: Union[_VT, _T]) -> Union[_VT, _T]: ...
    def lookup(
        self, key: type[_KT], default: Union[Missing, _VT, _T] = MISSING
    ) -> Union[_VT, _T]:
        found_cls_key = self.lookup_cls(key)
        if found_cls_key is not None:
            return self[found_cls_key]
        if default is MISSING:
            raise KeyError(f"{key} cannot be correctly resolved in {self.namespace}.")
        return default
