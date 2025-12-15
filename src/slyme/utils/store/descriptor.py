from slyme.utils.typing import (
    Generic,
    TypeVar,
    Any,
    overload,
    Union,
    Self,
    Protocol,
)
from . import Key

_KeyT = TypeVar("_KeyT", bound=Key)


class _PathFactory(Protocol[_KeyT]):
    def __call__(self, path: str, *args, **kwargs) -> _KeyT: ...


class KeyField(Generic[_KeyT]):
    """
    Descriptor declaring a Key dependency/production.
    Strictly forbids default creation logic on access.
    Supports explicit deletion via 'del'.
    """

    def __init__(
        self, path_factory: Union[_PathFactory[_KeyT], None] = None, doc: str = ""
    ):
        self.path_factory = path_factory
        self.doc = doc

    def __set_name__(self, owner: Any, name: str):
        self.name = name
        self.private_name = f"_key_field_{name}"

    def __getattr__(self, item):
        if item == "name" or item == "private_name":
            raise RuntimeError(
                f"KeyField attribute '{item}' is missing. "
                "This usually happens when the descriptor is not assigned to a class attribute "
                "correctly (missing __set_name__ call), or instantiated dynamically without manual setup."
            )
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{item}'"
        )

    @overload
    def __get__(self, instance: None, owner: Union[type, None] = None) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: Union[type, None] = None) -> _KeyT: ...
    def __get__(
        self, instance: Union[object, None], owner: Union[type, None] = None
    ) -> Union[Self, _KeyT]:
        if instance is None:
            return self

        try:
            return getattr(instance, self.private_name)
        except AttributeError:
            raise AttributeError(
                f"KeyField `{self.name}` has not been assigned a value yet. "
                f"You should initialize it first."
            )

    def __set__(self, instance: object, value: Union[str, _KeyT]):
        if isinstance(value, str):
            if self.path_factory is None:
                raise TypeError(
                    f"KeyField `{self.name}` does not allow string assignment "
                    f"(no `path_factory` provided). Expected `{Key}` instance."
                )
            value = self.path_factory(value)

        if not isinstance(value, Key):
            raise TypeError(
                f"Expected a `{Key}` instance for field `{self.name}`, got {type(value)}."
            )

        setattr(instance, self.private_name, value)

    def __delete__(self, instance: object):
        """"""
        try:
            delattr(instance, self.private_name)
        except AttributeError:
            raise AttributeError(
                f"KeyField `{self.name}` is not set, and cannot delete."
            )
