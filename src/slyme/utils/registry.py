"""
A convenient registry util that dynamically retrieves items based on keys.
"""

import importlib
from .collection import MutableMappingProxy
from .decorator import auto_decorator
from .common import FuncParams
from .typing import (
    Union,
    Iterable,
    TypeVar,
    overload,
    Callable,
    Mapping,
    Dict,
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
        load_mapping: Union[Mapping[_KT, Union[str, FuncParams]], Missing] = MISSING,
    ):
        super().__init__({})
        self._namespace = repr(self) if namespace is MISSING else namespace
        self._strict = strict
        self._load_mapping: Dict[_KT, Union[str, FuncParams]] = (
            {} if load_mapping is MISSING else dict(load_mapping)
        )

    def resolve_strict(self, strict: Union[bool, Missing] = MISSING):
        """
        Parse the given ``strict`` value. If ``strict`` is ``MISSING``, then
        return ``self.strict`` (i.e., the config of the registry), else
        directly return ``strict`` (which will override the registry config
        when registering a specific item).
        """
        return strict if strict is not MISSING else self._strict

    #
    # Register a single item using ``__call__``.
    #

    @overload
    def __call__(
        self,
        _obj: Missing = MISSING,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> Callable[[_VT2], _VT2]: ...
    @overload
    def __call__(
        self,
        _obj: _VT2,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2: ...
    @auto_decorator(index=1, keyword="_obj")
    def __call__(
        self,
        _obj: Union[_VT2, Missing] = MISSING,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2:
        """
        Register an item. Can be used as a decorator or a normal method.
        """

        def decorator(obj: _VT2) -> _VT2:
            # Call the core register method.
            self.register(obj, key, strict)
            return obj

        return decorator

    # Register a single item with multiple keys.
    @overload
    def register_multi(
        self,
        keys: Iterable[_KT],
        *,
        _obj: Missing = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> Callable[[_VT2], _VT2]: ...
    @overload
    def register_multi(
        self, keys: Iterable[_KT], *, _obj: _VT2, strict: Union[bool, Missing] = MISSING
    ) -> _VT2: ...
    @auto_decorator(keyword="_obj")
    def register_multi(
        self,
        keys: Iterable[_KT],
        *,
        _obj: Union[_VT2, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2:
        """
        Register an item with multiple keys.
        """
        strict = self.resolve_strict(strict)

        def decorator(obj: _VT2) -> _VT2:
            for key in keys:
                # Respectively register the item with different keys.
                self(_obj=obj, key=key, strict=strict)
            return obj

        return decorator

    #
    # The core register method.
    #

    def register(
        self, obj: _VT, key: Union[_KT, Missing], strict: Union[bool, Missing]
    ) -> None:
        """
        Core register method. Can be overridden by subclasses for extended features.
        """
        strict = self.resolve_strict(strict)
        if key is MISSING:
            # The key should be explicitly specified or be properly handled by subclasses,
            # so it should never be ``MISSING`` here.
            raise ValueError(
                f"Error when registering ``{repr(obj)}`` in registry ``{self._namespace}``. "
                f"Key cannot be ``MISSING``. Check the key setting."
            )
        if key in self and strict:
            raise ValueError(
                f"Key ``{key}`` already exists in registry ``{self._namespace}``."
            )
        # Register ``obj`` with ``key``.
        self[key] = obj

    # Lazy loading.
    def load(self, key: _KT) -> _VT:
        if key in self:
            return self[key]
        if key not in self._load_mapping:
            raise KeyError(
                f"The given key ``{key}`` does not exist in registry ``{self._namespace}`` or "
                "in the load_mapping. Check the registry settings."
            )
        # Import the module to load items.
        module_setting = self._load_mapping[key]
        if isinstance(module_setting, FuncParams):
            importlib.import_module(*module_setting.args, **module_setting.kwargs)
        else:
            importlib.import_module(module_setting)
        if key not in self:
            raise KeyError(
                f"The given key ``{key}`` still does not exist in registry ``{self._namespace}`` "
                f"after loading the module ``{module_setting}``. Check the registry settings."
            )
        return self[key]


class Registry(GeneralRegistry[str, _VT]):
    """
    A more commonly used registry with key type set to str.
    """

    def register(
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
                f"``{repr(obj)}`` in registry {self._namespace}. Neither is the ``key`` "
                f"param specified, nor does the attribute ``__name__`` exist in "
                f"``{repr(obj)}``."
            )
        return super().register(obj, key, strict)


class TypeRegistry(GeneralRegistry[type[_KT], _VT]):
    """
    Registry that uses Python types as keys and supports MRO-based lookup.
    """

    @overload
    def lookup(self, key: type[_KT], default: Missing = MISSING) -> _VT: ...
    @overload
    def lookup(self, key: type[_KT], default: Union[_VT, _T]) -> Union[_VT, _T]: pass
    def lookup(self, key: type[_KT], default: Union[Missing, _VT, _T] = MISSING) -> Union[_VT, _T]:
        for base in resolve_mro(key):
            if base in self:
                return self[base]
        if default is MISSING:
            raise KeyError(f"{key} cannot be correctly resolved in {self._namespace}.")
        else:
            return default
