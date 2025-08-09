import re
from slyme.utils.abc.base.observer import AttrObservableABC, AttrObserverABC
from .collection import BaseDict
from slyme.utils.decorator import auto_decorator, func_setattr
from slyme.utils.typing.extension import (
    MISSING,
    Missing,
    is_none_or_nothing,
    resolve_private_attr_name,
    unwrap_method,
    EmptyFlag,
)
from slyme.utils.typing.native import (
    Any,
    Callable,
    Dict,
    List,
    Sequence,
    Set,
    Union,
    overload,
)
from . import BaseObjectInit
from .attr import with_attr_op_state

OBSERVE_FUNC_PREFIX = "smx_observe"
OBSERVE_FUNC_PREFIX_PATTERN = re.compile(f"^{OBSERVE_FUNC_PREFIX}")
OBSERVE_INIT = "smx_observe_init"
OBSERVE_NAMESPACE = "smx_observe_namespace"
ObserveFuncType = Callable[[Any, Any, "AttrObservable"], None]


class _AttrObservableInfo:

    def __init__(
        self, observable: "AttrObservable", attr_set: Union[Set[str], Missing] = MISSING
    ) -> None:
        self.observable = observable
        self.attr_set: Set[str] = set() if attr_set is MISSING else attr_set

    def smx_add_attr(self, __name: str) -> None:
        return self.attr_set.add(__name)

    def smx_remove_attr(self, __name: str) -> None:
        if __name in self.attr_set:
            self.attr_set.remove(__name)

    def smx_is_empty(self) -> bool:
        return len(self.attr_set) < 1


class _AttrObservableDict(BaseDict[str, _AttrObservableInfo]):

    @staticmethod
    def smx_get_observable_id(__observable: "AttrObservable") -> str:
        # this behavior may change through different ``slyme`` versions
        return str(id(__observable))

    def smx_add(self, __observable: "AttrObservable", __name: str) -> None:
        observable_id = self.smx_get_observable_id(__observable)

        if observable_id not in self:
            self[observable_id] = _AttrObservableInfo(__observable)
        self[observable_id].smx_add_attr(__name)

    def smx_remove(self, __observable: "AttrObservable", __name: str) -> None:
        observable_id = self.smx_get_observable_id(__observable)

        if observable_id in self:
            observable_info = self[observable_id]
            observable_info.smx_remove_attr(__name)
            if observable_info.smx_is_empty():
                del self[observable_id]

    def smx_get(self, __observable: "AttrObservable") -> Set[str]:
        observable_id = self.smx_get_observable_id(__observable)

        if observable_id in self:
            return self[observable_id].attr_set
        else:
            return set()

    def smx_contains(self, __observable: "AttrObservable") -> bool:
        return self.smx_get_observable_id(__observable) in self


class AttrObserver(AttrObserverABC):

    def __init__(self) -> None:
        self.__observable_dict = _AttrObservableDict()

    @staticmethod
    def smx_check_namespace(
        func: ObserveFuncType, namespaces: Union[Sequence[str], EmptyFlag]
    ) -> bool:
        return (
            # ``None`` or ``NOTHING`` namespace won't match any function.
            not is_none_or_nothing(namespaces)
            and (
                # ``MISSING`` namespace will match all the functions.
                namespaces is MISSING
                or
                # Otherwise check if the function's namespace exists in ``namespaces``.
                getattr(unwrap_method(func), OBSERVE_NAMESPACE, MISSING) in namespaces
            )
        )

    def smx_detach_inspect(
        self, namespaces: Union[Sequence[str], EmptyFlag] = MISSING
    ) -> Dict[str, ObserveFuncType]:
        return self.smx_observe_inspect(
            # Check namespace.
            lambda func: self.smx_check_namespace(func, namespaces)
        )

    def smx_attach_inspect(
        self, namespaces: Union[Sequence[str], EmptyFlag] = MISSING
    ) -> Dict[str, ObserveFuncType]:
        return self.smx_observe_inspect(
            # Check namespace.
            lambda func: self.smx_check_namespace(func, namespaces)
        )

    def smx_observe_inspect(
        self, __func: Callable[[ObserveFuncType], bool]
    ) -> Dict[str, ObserveFuncType]:
        """
        Inspect the observe attributes.
        """
        observe_dict: Dict[str, ObserveFuncType] = {}
        """
        NOTE: ``func_name`` here is actually the attribute name in the object, rather than the 
        real function name.

        Example:
            ```Python
            class A: pass

            def b(): pass

            a = A()
            a.c = b  # The ``func_name`` is ``c`` rather than ``b``
            ```
        """
        for func_name in (
            func_name
            for func_name in dir(self)
            if OBSERVE_FUNC_PREFIX_PATTERN.search(func_name) is not None
        ):
            func: ObserveFuncType = getattr(self, func_name)
            # inspect checking
            if __func(func):
                observe_attr_name = OBSERVE_FUNC_PREFIX_PATTERN.sub("", func_name)
                observe_dict[observe_attr_name] = func
        return observe_dict

    def smx_detach_all(self) -> None:
        # NOTE: create a new list of ``__observable_dict.values()`` to
        # avoid value change during iteration.
        for observable_info in list(self.__observable_dict.values()):
            observable_info.observable.smx_detach(self)

    def __del__(self) -> None:
        self.smx_detach_all()

    def smx_get_observable_dict(self) -> _AttrObservableDict:
        return self.__observable_dict


def get_observe_func_name(name: str) -> str:
    return f"{OBSERVE_FUNC_PREFIX}{name}"


class _AttrObserverDict(BaseDict[str, List[AttrObserver]]):

    def smx_add(self, __name: str, __observer: AttrObserver) -> None:
        if __name not in self:
            self[__name] = []

        observers = self[__name]
        if __observer not in observers:
            observers.append(__observer)

    def smx_remove(self, __name: str, __observer: AttrObserver) -> None:
        if __name in self:
            observers = self[__name]
            if __observer in observers:
                observers.remove(__observer)
            if len(observers) < 1:
                del self[__name]


class AttrObservable(AttrObservableABC[AttrObserver], BaseObjectInit):
    """
    NOTE: The ``__init__`` method of ``AttrObservable`` should always be called
    first before other attributes can be set.
    """

    @with_attr_op_state(active=False)
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # attr name to observers
        self.__attr_observer_dict: _AttrObserverDict
        # TODO: self.xx = yy
        # Use ``object.__setattr__`` to escape from any custom attribute operations.
        object.__setattr__(
            self,
            resolve_private_attr_name(AttrObservable, "__attr_observer_dict"),
            _AttrObserverDict(),
        )

    def smx_attach(
        self,
        __observer: AttrObserver,
        *,
        init: Union[bool, Missing] = MISSING,
        namespaces: Union[Sequence[str], EmptyFlag] = MISSING,
    ) -> None:
        observe_dict = __observer.smx_attach_inspect(namespaces)

        names = set(observe_dict.keys())
        # inspect new observe attrs
        names = names - __observer.smx_get_observable_dict().smx_get(self)

        for name in names:
            attr_init = (
                getattr(unwrap_method(observe_dict[name]), OBSERVE_INIT, True)
                if init is MISSING
                else init
            )
            self.smx_attach_attr(__observer, name, init=bool(attr_init))

    def smx_attach_attr(
        self, __observer: AttrObserver, __name: str, *, init: bool = True
    ) -> None:
        self.__attr_observer_dict.smx_add(__name, __observer)
        __observer.smx_get_observable_dict().smx_add(self, __name)

        if init:
            value = getattr(self, __name, MISSING)
            self.smx_notify(__observer, __name, value, MISSING)

    def smx_detach(
        self,
        __observer: AttrObserver,
        *,
        namespaces: Union[Sequence[str], EmptyFlag] = MISSING,
    ) -> None:
        observable_dict = __observer.smx_get_observable_dict()
        if not observable_dict.smx_contains(self):
            return
        # Check names to detach.
        detach_names = observable_dict.smx_get(self)
        detach_names = (
            set(__observer.smx_detach_inspect(namespaces).keys()) & detach_names
        )

        # NOTE: use a copy of ``detach_names`` to avoid value change during iteration
        for name in list(detach_names):
            self.smx_detach_attr(__observer, name)

    def smx_detach_attr(self, __observer: AttrObserver, __name: str) -> None:
        self.__attr_observer_dict.smx_remove(__name, __observer)
        __observer.smx_get_observable_dict().smx_remove(self, __name)

    def smx_notify(
        self, __observer: AttrObserver, __name: str, __new_value: Any, __old_value: Any
    ) -> None:
        """
        Notify one observer with new value, old value and observable object.
        """
        func: ObserveFuncType = getattr(__observer, get_observe_func_name(__name))
        return func(__new_value, __old_value, self)

    def __setattr__(self, __name: str, __value: Any) -> None:
        if (
            __name in _ATTR_OBSERVABLE_ESCAPED_SETATTRS
            or __name not in self.__attr_observer_dict
        ):
            return super().__setattr__(__name, __value)
        else:
            old_value = getattr(self, __name, MISSING)
            super().__setattr__(__name, __value)
            # observer is called only when the new value is different from the old value
            if __value is not old_value:
                for observer in self.__attr_observer_dict[__name]:
                    self.smx_notify(observer, __name, __value, old_value)

    def smx_get_attr_observer_dict(self) -> _AttrObserverDict:
        return self.__attr_observer_dict


# These attributes are escaped from ``__setattr__`` to avoid circular or infinite
# recursion problems.
_ATTR_OBSERVABLE_ESCAPED_SETATTRS = frozenset(
    [resolve_private_attr_name(AttrObservable, "__attr_observer_dict")]
)


@overload
def attr_observe(
    _func: Missing = MISSING,
    *,
    init: bool = True,
    namespace: Union[str, Missing] = MISSING,
) -> Callable[[ObserveFuncType], ObserveFuncType]: ...
@overload
def attr_observe(
    _func: ObserveFuncType,
    *,
    init: bool = True,
    namespace: Union[str, Missing] = MISSING,
) -> ObserveFuncType: ...
@auto_decorator(index=0, keyword="_func")
def attr_observe(
    _func=MISSING, *, init: bool = True, namespace: Union[str, Missing] = MISSING
):
    """
    Set observe settings to the observe func.
    """

    def decorator(func: ObserveFuncType) -> ObserveFuncType:
        return func_setattr(
            func, attr_dict={OBSERVE_INIT: init, OBSERVE_NAMESPACE: namespace}
        )

    return decorator
