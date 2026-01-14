"""
A convenient registry util that dynamically retrieves items based on keys.
"""

import inspect
from collections.abc import Callable, Iterable
from typing import (
    Union,
    TypeVar,
    overload,
)
from .collection import MutableMappingProxy
from .decorator import auto_decorator
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

    def unregister(self, key: _KT, *, strict: Union[bool, Missing] = MISSING) -> None:
        """
        Unregister an item by its key.
        """
        strict = self._resolve_strict(strict)
        try:
            del self[key]
        except KeyError:
            if strict:
                raise

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
    Registry that uses Python types as keys and supports inheritance lookup.
    """

    def __init__(
        self,
        namespace: Union[str, Missing] = MISSING,
        /,
        *,
        strict: bool = True,
        orthogonal: bool = False,
    ):
        super().__init__(namespace, strict=strict)
        self.orthogonal = orthogonal

    def _register(self, obj: _VT, key: type[_KT], strict: Union[bool, Missing]) -> None:
        if not isinstance(key, type):
            raise TypeError(f"TypeRegistry key must be a class, got {type(key)}.")
        # Check orthogonal.
        if self.orthogonal:
            parents = []
            children = []
            for existing in self.keys():
                if existing is key:
                    continue
                if issubclass(key, existing):
                    parents.append(existing)
                elif issubclass(existing, key):
                    children.append(existing)
            if parents or children:
                error_msgs = []
                if parents:
                    error_msgs.append(f"Conflict with existing parents: {parents}")
                if children:
                    error_msgs.append(f"Conflict with existing children: {children}")
                raise ValueError(
                    f"Orthogonal conflict when registering {key} in {self.namespace}:\n"
                    + "\n".join(error_msgs)
                )
        super()._register(obj, key, strict)

    def lookup_cls(
        self, key: type[_KT], *, reverse: bool = False
    ) -> Union[type[_KT], None]:
        """
        Walk through the MRO of the given ``key`` (a class) and return the first
        base class (KEY) that is registered in this registry.

        **NOTE on Virtual Subclasses:**
        This method strictly relies on the standard Python Method Resolution Order
        (MRO) to resolve the most specific (or generic) registered key.
        Therefore, it **does not** support virtual subclasses (e.g., classes
        registered via ``abc.ABCMeta.register``), because virtual ancestors do
        not appear in a class's ``__mro__``.

        Args:
            key: The class to lookup.
            reverse (bool):
                If ``True``, lookup from the end of MRO (finding the most generic registered base).
                If ``False`` (default), lookup from the start of MRO (finding the most specific registered base).
        """
        mro = inspect.getmro(key)
        if reverse:
            mro = reversed(mro)
        for base in mro:
            if base in self:
                return base
        return None

    @overload
    def lookup(
        self, key: type[_KT], default: Missing = MISSING, *, reverse: bool = False
    ) -> _VT: ...
    @overload
    def lookup(
        self, key: type[_KT], default: Union[_VT, _T], *, reverse: bool = False
    ) -> Union[_VT, _T]: ...
    def lookup(
        self,
        key: type[_KT],
        default: Union[Missing, _VT, _T] = MISSING,
        *,
        reverse: bool = False,
    ) -> Union[_VT, _T]:
        """
        Lookup the value associated with the first registered base class of ``key``
        following the MRO.

        See ``lookup_cls`` for details on MRO-based resolution and the limitation
        regarding virtual subclasses.
        """
        found_cls_key = self.lookup_cls(key, reverse=reverse)
        if found_cls_key is not None:
            return self[found_cls_key]
        if default is MISSING:
            raise KeyError(f"{key} cannot be correctly resolved in {self.namespace}.")
        return default

    def lookup_all_cls(self, key: type[_KT]) -> Iterable[type[_KT]]:
        """
        Yield all **registered** keys that are superclasses (including virtual ones) of ``key``.

        NOTE: Unlike ``lookup_cls``, this method relies on ``issubclass`` check
        rather than MRO. The yield order follows the **registration order**,
        not the inheritance order.
        """
        for cls_key in self.keys():
            if issubclass(key, cls_key):
                yield cls_key

    def lookup_all(self, key: type[_KT]) -> Iterable[_VT]:
        """
        Yield all **registered** values whose keys are superclasses of the given ``key``.
        """
        for cls_key in self.lookup_all_cls(key):
            yield self[cls_key]

    def collect_cls(self, base_cls: type[_KT]) -> Iterable[type[_KT]]:
        """
        Yield all **registered** keys that are subclasses (including virtual ones) of ``base_cls``.

        This iterates over all registered keys and checks ``issubclass(registered_key, base_cls)``.
        """
        for cls_key in self.keys():
            if issubclass(cls_key, base_cls):
                yield cls_key

    def collect(self, base_cls: type[_KT]) -> Iterable[_VT]:
        """
        Collect and yield values of all **registered** keys that are subclasses of ``base_cls``.
        Includes ``base_cls`` itself if it is registered.
        """
        for cls_key in self.collect_cls(base_cls):
            yield self[cls_key]
