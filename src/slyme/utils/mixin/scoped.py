"""
Scoped lifecycle management.
"""

import slyme.logging.logger as logger
from .collection import MutableSequenceMixin
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
from slyme.utils.typing import (
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
from slyme.utils.constant import (
    EmptyFlag,
    MISSING,
    NOTHING,
    Stop,
    resolve_instance_classname,
    is_empty_flag,
)
from slyme.utils.mixin.attr import SetattrOpMixin, GetattrOpMixin, DelattrOpMixin
from slyme.utils.execution import (
    generator_context_manager,
    empty_context_manager,
    check_stop_flag,
    GeneratorExecutorCollection,
    generator_context_manager,
)
from slyme.utils.descriptor import (
    ReadonlyCachedAttr,
    get_descriptor_private_name,
)
from . import InitAdapterMixin
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
    MutableSequenceMixin[ScopedManagerABC[ScopedMixinABC, Any]],
    ScopedManagerCollectionABC[ScopedManagerABC[ScopedMixinABC, Any], ScopedMixinABC],
):
    """
    A collection that contains entered scoped managers.
    """

    pass


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


class AttrScopable(AttrScopableABC, InitAdapterMixin):
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
