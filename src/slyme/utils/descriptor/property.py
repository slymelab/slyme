from threading import RLock
from contextlib import ExitStack
from slyme.utils.typing import (
    TypeVar,
    Generic,
    Generator,
    Callable,
    Any,
    Union,
    Literal,
    Iterable,
    overload,
    Self,
)
from slyme.utils.mixin import InitAdapterMixin
from slyme.utils.collection import MutableSequenceProxy
from slyme.utils.collection.base import SequenceData
from slyme.utils.constant import STOP, MISSING, Missing
from slyme.utils.execution import (
    generator_context_manager,
    GeneratorExecutorCollection,
)

_T = TypeVar("_T")
_PropertyT = TypeVar("_PropertyT", bound="Property")
_GetT_co = TypeVar("_GetT_co", covariant=True)
_SetT_contra = TypeVar("_SetT_contra", contravariant=True)
PRIVATE_PROP_PREFIX = "_prop_"


def private_prop(name: str) -> str:
    return f"{PRIVATE_PROP_PREFIX}{name}"


class Property(InitAdapterMixin, Generic[_GetT_co, _SetT_contra]):
    """Descriptor base class.

    See ``DescriptorABC`` for more details.
    """

    def __init__(self, /, *, allow_get_missing: bool = False, **kwargs) -> None:
        """Initializes the base descriptor class.

        Args:
            allow_get_missing (bool, optional):
                Whether returns ``MISSING`` in ``__get__`` when the private descriptor attribute does
                not exist. Defaults to False.
        """
        super().__init__(**kwargs)
        self.allow_get_missing = allow_get_missing

    def __set_name__(self, owner, name) -> None:
        self.public_name = name
        self.private_name = private_prop(name)

    def __set__(self, instance, value: _SetT_contra) -> None:
        with generator_context_manager(self.set_yield(instance, value)) as value:
            if value is STOP:
                return
            return setattr(instance, self.private_name, value)

    @overload
    def __get__(self, instance: None, owner: Union[type, None] = None) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: Union[type, None] = None) -> _GetT_co: ...
    def __get__(self, instance: Union[object, None], owner: Union[type, None] = None) -> Union[_GetT_co, Missing, Self]:
        if instance is None:
            return self

        if self.allow_get_missing:
            value = getattr(instance, self.private_name, MISSING)
        else:
            value = getattr(instance, self.private_name)
        with generator_context_manager(self.get_yield(instance, owner, value)) as value:
            if value is STOP:
                return MISSING
            return value

    def __delete__(self, instance):
        with generator_context_manager(self.delete_yield(instance)) as value:
            if value is STOP:
                return
            return delattr(instance, self.private_name)

    def set_yield(self, instance, value) -> Generator:
        yield value

    def get_yield(self, instance, owner, value) -> Generator:
        yield value

    def delete_yield(self, instance) -> Generator:
        yield


class ReadonlyProperty(Property[_GetT_co, _SetT_contra]):
    """Make the instance attribute readonly."""

    def __init__(
        self,
        *,
        allow_get_missing=False,
        missing_readonly: bool = False,
        check_mode: Literal["storage", "access"] = "storage",
    ):
        """Initializes the ``ReadonlyDescriptor``.

        Args:
            allow_get_missing (bool, optional): See ``Descriptor.__init__``. Defaults to False.
            missing_readonly (bool, optional):
                Whether the attribute is still readonly when it does not exist yet. Defaults to False.
            check_mode (Literal["storage", "access"], optional):
                - "storage": ``ReadonlyDescriptor`` will only check the attribute existence of
                ``private_name``.
                - "access": ``ReadonlyDescriptor`` will check the direct access to the attribute (i.e.,
                ``public_name`` will be checked).
        """
        super().__init__(allow_get_missing=allow_get_missing)
        self.missing_readonly = missing_readonly
        self.check_mode = check_mode

    def set_yield(self, instance, value) -> Generator:
        if self._check_attr_mod(instance):
            yield value
        else:
            raise AttributeError(
                f"``{self.public_name}`` in class ``{type(instance)}`` is a readonly attribute. You are trying to set a new value to it."
            )

    def delete_yield(self, instance) -> Generator:
        if self._check_attr_mod(instance):
            yield
        else:
            raise AttributeError(
                f"``{self.public_name}`` in class ``{type(instance)}`` is a readonly attribute. You are trying to delete it."
            )

    def _check_attr_mod(self, instance) -> bool:
        """Check whether the attribute modification is legal.

        Returns:
            bool: Indicates whether the attribute modification is legal.
        """
        if self.check_mode == "access":
            # There are two possible situations when the attribute does not exist:
            # 1. ``allow_get_missing=False``, then the following ``getattr`` function will return the
            # default value ``MISSING``.
            # 2. ``allow_get_missing=True``, then the following ``getattr`` function will return the
            # ``MISSING`` value returned by ``__get__``.
            # Therefore, we can just check whether the return value is ``MISSING`` to decide whether the
            # attribute does not exist.
            is_missing = getattr(instance, self.public_name, MISSING) is MISSING
        elif self.check_mode == "storage":
            # Directly check private storage
            is_missing = getattr(instance, self.private_name, MISSING) is MISSING
        else:
            raise ValueError(f"Check mode {self.check_mode} not supported.")

        return (
            # When ``self.missing_readonly`` is ``True``, then the attribute is always readonly,
            # so it should directly return ``False``.
            not self.missing_readonly
            and is_missing
        )


class PropertyContainer(
    Property[_GetT_co, _SetT_contra],
    MutableSequenceProxy[_PropertyT],
    Generic[_PropertyT, _GetT_co, _SetT_contra],
):
    """Container that supports a set of ``Descriptor``s.

    See ``DescriptorContainerABC`` for more details.
    """

    def __init__(
        self,
        /,
        *,
        allow_get_missing=False,
        properties: SequenceData[_PropertyT] = None,
        **kwargs,
    ):
        super().__init__(
            allow_get_missing=allow_get_missing, sequence_data=properties, **kwargs
        )

    def __set_name__(self, owner, name):
        """Call ``__set_name__`` to all the descriptors."""
        super().__set_name__(owner, name)
        for descriptor in self:
            descriptor.__set_name__(owner, name)

    def set_yield(self, instance, value) -> Generator:
        with ExitStack() as stack:
            for descriptor in self:
                value = stack.enter_context(
                    generator_context_manager(descriptor.set_yield(instance, value))
                )
                # If the context manager returns ``STOP``, then directly break.
                if value is STOP:
                    break
            yield value

    def get_yield(self, instance, owner, value) -> Generator:
        with ExitStack() as stack:
            for descriptor in self:
                value = stack.enter_context(
                    generator_context_manager(
                        descriptor.get_yield(instance, owner, value)
                    )
                )
                # If the context manager returns ``STOP``, then directly break.
                if value is STOP:
                    break
            yield value

    def delete_yield(self, instance) -> Generator:
        with GeneratorExecutorCollection(
            (descriptor.delete_yield(instance) for descriptor in self)
        ).stack_context_manager() as vals:
            # NOTE: ``STOP`` is automatically checked by ``__delete__``
            yield (vals[-1] if len(vals) > 0 else None)


class CachedProperty(Property[_GetT_co, _SetT_contra]):
    def __init__(self, /, factory: Callable[[Any], _GetT_co]):
        # NOTE: Should enable ``allow_get_missing`` here to avoid ``AttributeError``.
        super().__init__(allow_get_missing=True)
        self.factory = factory
        self.lock = RLock()

    def get_yield(self, instance, owner, value) -> Generator:
        if value is MISSING:
            # NOTE: Use double-check lock to keep consistency in multithreading scenarios.
            with self.lock:
                current_value = getattr(instance, self.private_name, MISSING)
                if current_value is MISSING:
                    new_value = self.factory(instance)
                    setattr(instance, self.private_name, new_value)
                    yield new_value
                else:
                    yield current_value
        else:
            yield value


class ReadonlyCachedProperty(
    PropertyContainer[Property, _GetT_co, _SetT_contra]
):
    def __init__(self, /, factory: Callable[[Any], _GetT_co], **kwargs):
        super().__init__(
            allow_get_missing=True,
            properties=[
                # NOTE: Can only set attribute through ``factory`` function, so
                # ``missing_readonly`` is set to ``True`` to avoid setattr in
                # advance anywhere else.
                ReadonlyProperty(missing_readonly=True),
                CachedProperty(factory),
            ],
            **kwargs,
        )
