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
    Generic,
    Mapping,
    Dict,
)
from .constant import MISSING, Missing

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")
_VT2 = TypeVar("_VT2")


class GeneralRegistry(MutableMappingProxy[_KT, _VT], Generic[_KT, _VT]):
    """
    A general registry whose type of keys can be any specified value.

    We name the parameter in the methods ``cls`` (or ``_cls``) because at
    first the registry is designed for classes, and for compatibility we
    have not renamed the parameter (nor will we in the future).

    WARNING: You should avoid instantiating ``GeneralRegistry`` in any main
    scripts. It should be created in other non-main modules and imported by
    the main scripts instead.
    """

    def __init__(
        self,
        namespace: str,
        *,
        strict: bool = True,
        load_mapping: Union[Mapping[_KT, Union[str, FuncParams]], Missing] = MISSING,
    ):
        super().__init__({})
        self.__namespace = namespace
        self.strict__ = strict
        self.load_mapping__: Dict[_KT, Union[str, FuncParams]] = (
            {} if load_mapping is MISSING else dict(load_mapping)
        )

    def smx_get_namespace(self) -> str:
        """
        Get the namespace of the registry.
        """
        return self.__namespace

    def smx_parse_strict(self, strict: Union[bool, Missing] = MISSING):
        """
        Parse the given ``strict`` value. If ``strict`` is ``MISSING``, then
        return ``self.strict`` (i.e., the config of the registry), else
        directly return ``strict`` (which will override the registry config
        when registering a specific item).
        """
        return strict if strict is not MISSING else self.strict__

    #
    # Register a single item using ``__call__``.
    #

    @overload
    def __call__(
        self,
        _cls: Missing = MISSING,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> Callable[[_VT2], _VT2]:
        pass

    @overload
    def __call__(
        self,
        _cls: _VT2,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2:
        pass

    @auto_decorator(index=1, keyword="_cls")
    def __call__(
        self,
        _cls: Union[_VT2, Missing] = MISSING,
        *,
        key: Union[_KT, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2:
        """
        Register an item. Can be used as a decorator or a normal method.
        """

        def decorator(cls: _VT2) -> _VT2:
            # Call the core register method.
            self.smx_register(cls, key, strict)
            return cls

        return decorator

    #
    # Register a single item with multiple keys.
    #

    @overload
    def smx_register_multi(
        self,
        keys: Iterable[_KT],
        *,
        _cls: Missing = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> Callable[[_VT2], _VT2]:
        pass

    @overload
    def smx_register_multi(
        self, keys: Iterable[_KT], *, _cls: _VT2, strict: Union[bool, Missing] = MISSING
    ) -> _VT2:
        pass

    @auto_decorator(keyword="_cls")
    def smx_register_multi(
        self,
        keys: Iterable[_KT],
        *,
        _cls: Union[_VT2, Missing] = MISSING,
        strict: Union[bool, Missing] = MISSING,
    ) -> _VT2:
        """
        Register an item with multiple keys.
        """
        strict = self.smx_parse_strict(strict)

        def decorator(cls: _VT2) -> _VT2:
            for key in keys:
                # Respectively register the item with different keys.
                self(_cls=cls, key=key, strict=strict)
            return cls

        return decorator

    #
    # The core register method.
    #

    def smx_register(
        self, cls: _VT, key: Union[_KT, Missing], strict: Union[bool, Missing]
    ) -> None:
        """
        Core register method. Can be overridden by subclasses for extended features.
        """
        strict = self.smx_parse_strict(strict)
        if key is MISSING:
            # The key should be explicitly specified or be properly handled by subclasses,
            # so it should never be ``MISSING`` here.
            namespace = self.smx_get_namespace()
            raise ValueError(
                f"Error when registering ``{repr(cls)}`` in registry ``{namespace}``. "
                f"Key cannot be ``MISSING``. Check the key setting."
            )
        if key in self and strict:
            namespace = self.smx_get_namespace()
            raise ValueError(
                f"Key ``{key}`` already exists in registry ``{namespace}``."
            )
        # Register ``cls`` with ``key``.
        self[key] = cls

    #
    # Lazy loading.
    #

    def smx_load(self, key: _KT) -> _VT:
        if key in self:
            return self[key]
        if key not in self.load_mapping__:
            namespace = self.smx_get_namespace()
            raise KeyError(
                f"The given key ``{key}`` does not exist in registry ``{namespace}`` or "
                "in the load_mapping. Check the registry settings."
            )
        # Import the module to load items.
        module_setting = self.load_mapping__[key]
        if isinstance(module_setting, FuncParams):
            importlib.import_module(*module_setting.args, **module_setting.kwargs)
        else:
            importlib.import_module(module_setting)
        if key not in self:
            namespace = self.smx_get_namespace()
            raise KeyError(
                f"The given key ``{key}`` still does not exist in registry ``{namespace}`` "
                f"after loading the module ``{module_setting}``. Check the registry settings."
            )
        return self[key]


class Registry(GeneralRegistry[str, _VT], Generic[_VT]):
    """
    A more commonly used registry with key type set to str.
    """

    def smx_register(
        self, cls: _VT, key: Union[str, Missing], strict: Union[bool, Missing]
    ) -> None:
        """
        Core register method. If ``key`` is not specified, get the ``__name__`` of
        ``cls`` as ``key``.
        """
        if key is MISSING:
            # Try to get the ``__name__`` of ``cls`` if ``key`` is not specified.
            key = getattr(cls, "__name__", MISSING)
        if key is MISSING:
            namespace = self.smx_get_namespace()
            raise ValueError(
                f"Registry cannot correctly infer the ``key`` when registering "
                f"``{repr(cls)}`` in registry {namespace}. Neither is the ``key`` "
                f"param specified, nor does the attribute ``__name__`` exist in "
                f"``{repr(cls)}``."
            )
        return super().smx_register(cls, key, strict)
