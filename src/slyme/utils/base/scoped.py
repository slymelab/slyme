"""
Scoped lifecycle management.
"""

import slyme.logging.logger as logger
from .collection import BaseList
from slyme.utils.exception import APIMisused
from slyme.utils.abc.base.scoped import (
    ScopedContextStackABC,
    ScopedManagerABC,
    ScopedManagerCollectionABC,
    ScopedMixinABC,
    AttrScopableABC,
    ScopedGetattrGuardABC,
    ScopedSetattrGuardABC,
    ScopedDelattrGuardABC,
    ScopedAttrGuardCollectionABC,
)
from slyme.utils.typing.native import (
    Callable,
    Generator,
    Generic,
    Any,
    Union,
    Iterable,
    Tuple,
    Dict,
    ContextManager,
    TypeVar,
    cast,
    Mapping,
)
from slyme.utils.typing.extension import (
    EmptyFlag,
    MISSING,
    NOTHING,
    Stop,
    resolve_instance_classname,
    is_empty_flag,
)
from slyme.utils.base.attr import SetattrOpMixin, GetattrOpMixin, DelattrOpMixin
from slyme.utils.execution import (
    generator_context_manager,
    empty_context_manager,
    check_stop_flag,
    GeneratorExecutorCollection,
    generator_context_manager,
)
from slyme.utils.descriptor import (
    ReadonlyCachedDescriptor,
    get_descriptor_private_name,
)
from . import BaseObjectInit
from .attr import AttrOpState

_EnterT_co = TypeVar("_EnterT_co", covariant=True)
# NOTE: The ``ScopedManager`` may accept plain objects that are not
# instances of ``ScopedABC``.
_GeneralScopedT = TypeVar("_GeneralScopedT", bound=Union[ScopedMixinABC, Any])
# NOTE: The ``ScopedGuard`` can only accept ``ScopedABC`` objects.
_ScopableT = TypeVar("_ScopableT", bound=ScopedMixinABC)


class ScopedManager(
    ScopedManagerABC[_GeneralScopedT, _EnterT_co], Generic[_GeneralScopedT, _EnterT_co]
):
    """
    ``ScopedManager`` defines a generator method API used for scoped
    lifecycle management.
    """

    def call_scoped_manager_yield(
        self, scoped: _GeneralScopedT
    ) -> generator_context_manager[_EnterT_co, Any, Any]:
        manager_collection: Union[
            ScopedManagerCollectionABC[
                ScopedManagerABC[ScopedMixinABC, Any], ScopedMixinABC
            ],
            EmptyFlag,
        ] = getattr(scoped, "smx_scoped_managers", MISSING)

        # TODO: change to yield
        asdfasdf
        ctxgen = generator_context_manager(self.scoped_manager_yield(scoped))
        if is_empty_flag(manager_collection):
            # Disable traceback.
            return ctxgen
        else:
            manager_collection = cast(
                ScopedManagerCollectionABC[
                    ScopedManagerABC[ScopedMixinABC, Any], ScopedMixinABC
                ],
                manager_collection,
            )

            def wrapper():
                """
                Wrapper generator function that sets traceback of ``ScopedManager``.
                """
                manager_collection.append(self)
                try:
                    with ctxgen as value:
                        yield value
                finally:
                    try:
                        # Use ``rindex__`` is faster, because it is stack-like.
                        del manager_collection[manager_collection.smx_rindex(self)]
                    except ValueError:
                        logger.core_logger.warning(
                            f"ScopedManager ``{str(self)}`` is not found in ``{str(scoped)}``. "
                            "Maybe external modifications have been made to the traceback collection."
                        )

            return generator_context_manager(wrapper())


class ScopedManagerCollection(
    BaseList[ScopedManagerABC[ScopedMixinABC, Any]],
    ScopedManagerCollectionABC[ScopedManagerABC[ScopedMixinABC, Any], ScopedMixinABC],
):
    """
    A collection that contains entered scoped managers.
    """

    pass


class ScopedGuard(
    ScopedGuardABC[_ScopableT, _EnterT_co],
    Generic[_ScopableT, _EnterT_co],
):
    """
    NOTE: We strongly recommend to explicitly raise an Exception rather than
    yield ``STOP`` to intercept the guarded operations, because there is no
    way to really know whether the operations have succeeded through the
    returned value of the methods like ``__setattr__`` (which always returns
    ``None``).
    """

    def setattr_guard_yield(
        self, __scoped: _ScopableT, __name: str, __value: Any
    ) -> Generator[Union[Stop, None], Any, Any]:
        # Do nothing here, and subclasses can optionally implement it.
        yield

    def getattr_guard_yield(
        self, __scoped: _ScopableT, __name: str
    ) -> Generator[Union[Stop, None], Any, Any]:
        # Do nothing here, and subclasses can optionally implement it.
        yield

    def delattr_guard_yield(
        self, __scoped: _ScopableT, __name: str
    ) -> Generator[Union[Stop, None], Any, Any]:
        # Do nothing here, and subclasses can optionally implement it.
        yield

    def call_scoped_manager_yield(
        self, scoped: _ScopableT
    ) -> generator_context_manager[_EnterT_co, Any, Any]:
        # TODO: use super.scoped_ctxgen
        if not isinstance(scoped, ScopedMixinABC):
            raise APIMisused(
                "``ScopedGuard`` can only be applied to instances of ``Scoped`` or "
                "``ScopedABC``."
            )

        guard_enabled = scoped.smx_scoped_attr_guard_op_state()
        if not guard_enabled:
            # Do nothing here.
            return empty_context_manager()

        def wrapper():
            """
            Wrapper generator function that manages guard collection.
            """
            guard_collection = scoped.smx_scoped_attr_guards
            guard_collection.append(self)
            try:
                # FIXME: scoped_yield?
                with generator_context_manager(
                    self.scoped_manager_yield(scoped)
                ) as value:
                    yield value
            finally:
                try:
                    # NOTE: Get the collection again, in case the reference
                    # has changed.
                    guard_collection = scoped.smx_scoped_attr_guards
                    # Use ``rindex__`` is faster, because it is stack-like.
                    del guard_collection[guard_collection.rindex__(self)]
                except ValueError:
                    logger.core_logger.warning(
                        f"ScopedGuard ``{str(self)}`` is not found in ``{str(scoped)}``. "
                        "Maybe external modifications have been made to the guard collection."
                    )

        return generator_context_manager(wrapper())


class ScopedAttrGuardCollection(
    ScopedAttrGuardCollectionABC[
        ScopedSetattrGuardABC[ScopedMixinABC],
        ScopedGetattrGuardABC[ScopedMixinABC],
        ScopedDelattrGuardABC[ScopedMixinABC],
        ScopedMixinABC,
    ],
):
    """
    A collection that contains entered scoped guards.
    """

    def scoped_setattr_guard(
        self,
        __scoped: ScopedMixinABC,
        __setattr_func: Callable[[str, Any], None],
        __name: str,
        __value: Any,
    ) -> None:
        with GeneratorExecutorCollection(
            (guard.setattr_guard_yield(__scoped, __name, __value) for guard in self)
        ).stack_context_manager() as vals:
            if check_stop_flag(vals):
                return
            return __setattr_func(__name, __value)

    def scoped_getattr_guard(
        self,
        __scoped: ScopedMixinABC,
        __getattr_func: Callable[[str], Any],
        __name: str,
    ) -> Any:
        with GeneratorExecutorCollection(
            (guard.getattr_guard_yield(__scoped, __name) for guard in self)
        ).stack_context_manager() as vals:
            if check_stop_flag(vals):
                # Return ``MISSING`` to denote that ``getattr`` is intercepted.
                return MISSING
            return __getattr_func(__name)

    def scoped_delattr_guard(
        self,
        __scoped: ScopedMixinABC,
        __delattr_func: Callable[[str], None],
        __name: str,
    ) -> None:
        with GeneratorExecutorCollection(
            (guard.delattr_guard_yield(__scoped, __name) for guard in self)
        ).stack_context_manager() as vals:
            if check_stop_flag(vals):
                return
            return __delattr_func(__name)


class ScopedMixin(
    SetattrOpMixin,
    GetattrOpMixin,
    DelattrOpMixin,
    ScopedMixinABC[ScopedManagerABC],
    BaseObjectInit,
):
    smx_escaped_setattrs = (
        "smx_scoped_managers",
        get_descriptor_private_name("smx_scoped_managers"),
        "smx_scoped_attr_guards",
        get_descriptor_private_name("smx_scoped_attr_guards"),
        "smx_scoped_attr_guard_op_state",
        get_descriptor_private_name("smx_scoped_attr_guard_op_state"),
        "smx_scoped",
    )
    smx_escaped_getattrs = tuple(smx_escaped_setattrs)
    smx_escaped_delattrs = tuple(smx_escaped_setattrs)

    @ReadonlyCachedDescriptor
    def smx_scoped_managers(self):
        return ScopedManagerCollection()

    @ReadonlyCachedDescriptor
    def smx_scoped_attr_guards(self):
        return ScopedAttrGuardCollection()

    @ReadonlyCachedDescriptor
    def smx_scoped_attr_guard_op_state(self):
        return AttrOpState()

    def smx_scoped(
        self, __scoped_managers: Union[Iterable[ScopedManagerABC], EmptyFlag] = MISSING
    ) -> ContextManager[Tuple]:
        # TODO
        if is_empty_flag(__scoped_managers):
            return GeneratorExecutorCollection(
                __scoped_managers
            ).stack_context_manager()
        else:
            return GeneratorExecutorCollection(
                (
                    manager.call_scoped_manager_yield(self)
                    for manager in cast(Iterable[ScopedManagerABC], __scoped_managers)
                )
            ).stack_context_manager()

    def smx_setattr_op(self, name, value):
        if not self.smx_scoped_attr_guard_op_state.active:
            return super().smx_setattr_op(name, value)
        return self.smx_scoped_attr_guards.scoped_getattr_guard(
            self, super().smx_setattr_op, name, value
        )

    def smx_getattr_op(self, name):
        # TODO
        smx_scoped_attr_guard_op_state = self.smx_scoped_attr_guard_op_state
        return super().smx_getattr_op(name)

    def smx_delattr_op(self, name):
        return super().smx_delattr_op(name)


#
# Scoped Attribute.
#


class ScopedAttrRestore(ScopedManager[_GeneralScopedT, Any], Generic[_GeneralScopedT]):

    def __init__(self, attrs: Iterable[str]) -> None:
        self.attrs = list(attrs)
        self.prev_value_dict: Dict[str, Any] = {}

    def scoped_manager_yield(
        self, scoped: _GeneralScopedT
    ) -> Generator["ScopedAttrRestore[_GeneralScopedT]", Any, Any]:
        for attr in self.attrs:
            # Only cache existing attributes of ``obj``.
            if hasattr(scoped, attr):
                self.prev_value_dict[attr] = getattr(scoped, attr, NOTHING)
        try:
            yield self
        finally:
            for attr in self.attrs:
                # Restore the attributes.
                try:
                    if attr in self.prev_value_dict:
                        # Restore previously existing attributes before the scope.
                        setattr(scoped, attr, self.prev_value_dict[attr])
                    elif hasattr(scoped, attr):
                        # Remove previously non-existing attributes before the scope.
                        delattr(scoped, attr)
                except Exception as e:
                    logger.core_logger.error(
                        f"Restoring scoped attribute failed. Object: {str(scoped)}, "
                        f"attribute: {attr}. {resolve_instance_classname(e)}: {str(e)}"
                    )
            # NOTE: Should clear the ``prev_value_dict`` for reuse.
            self.prev_value_dict.clear()


class ScopedAttrAssign(ScopedAttrRestore[_GeneralScopedT], Generic[_GeneralScopedT]):

    def __init__(self, attr_assign: Mapping[str, Any]) -> None:
        super().__init__(attr_assign.keys())
        self.attr_assign = attr_assign

    def scoped_manager_yield(
        self, scoped: _GeneralScopedT
    ) -> Generator["ScopedAttrAssign[_GeneralScopedT]", Any, Any]:
        with generator_context_manager(super().scoped_manager_yield(scoped)):
            for attr, value in self.attr_assign.items():
                try:
                    setattr(scoped, attr, value)
                except Exception as e:
                    logger.core_logger.error(
                        f"Assigning scoped attribute failed. Object: {str(scoped)}, "
                        f"attribute: {attr}. {resolve_instance_classname(e)}: {str(e)}"
                    )
            yield self


class AttrScopable(AttrScopableABC, BaseObjectInit):
    """
    Helper class that implements ``ScopedAttrAssign`` and ``ScopedAttrRestore``
    through methods.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def smx_assign(
        self, attr_assign: Mapping[str, Any]
    ) -> generator_context_manager[ScopedAttrAssign, Any, Any]:
        return ScopedAttrAssign(attr_assign).call_scoped_manager_yield(self)

    def smx_restore(
        self, attrs: Iterable[str]
    ) -> generator_context_manager[ScopedAttrRestore, Any, Any]:
        return ScopedAttrRestore(attrs).call_scoped_manager_yield(self)
