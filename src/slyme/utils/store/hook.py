"""Store hooks."""

from dataclasses import dataclass
from typing import Union, Any, TYPE_CHECKING, TypeVar
from slyme.utils.constant import Missing
from slyme.utils.collection import MutableSequenceProxy

if TYPE_CHECKING:
    from .store import Store, Ref
_StoreHookT = TypeVar("_StoreHookT", bound="StoreHook")


class StoreHook:
    def on_getitem(self, instance: "Store", key: "Ref", value: Any, /) -> None:
        pass

    def on_setitem(
        self,
        instance: "Store",
        key: "Ref",
        old_value: Union[Missing, Any],
        new_value: Any,
        /,
    ) -> None:
        pass

    def on_delitem(
        self, instance: "Store", key: "Ref", old_value: Union[Missing, Any], /
    ) -> None:
        pass


class StoreHookList(StoreHook, MutableSequenceProxy[_StoreHookT]):
    def on_getitem(self, instance: "Store", key: "Ref", value: Any, /) -> None:
        for hook in self:
            hook.on_getitem(instance, key, value)

    def on_setitem(
        self,
        instance: "Store",
        key: "Ref",
        old_value: Union[Missing, Any],
        new_value: Any,
        /,
    ) -> None:
        for hook in self:
            hook.on_setitem(instance, key, old_value, new_value)

    def on_delitem(
        self, instance: "Store", key: "Ref", old_value: Union[Missing, Any], /
    ) -> None:
        for hook in self:
            hook.on_delitem(instance, key, old_value)


# Record hook
@dataclass(frozen=True)
class GetitemRecord:
    __slots__ = ("instance", "key", "value")
    instance: "Store"
    key: "Ref"
    value: Any


@dataclass(frozen=True)
class SetitemRecord:
    __slots__ = ("instance", "key", "old_value", "new_value")
    instance: "Store"
    key: "Ref"
    old_value: Any
    new_value: Any


@dataclass(frozen=True)
class DelitemRecord:
    __slots__ = ("instance", "key", "old_value")
    instance: "Store"
    key: "Ref"
    old_value: Any


class RecordHook(StoreHook):
    def __init__(self):
        super().__init__()
        self.records: list[Union[GetitemRecord, SetitemRecord, DelitemRecord]] = []

    def on_getitem(self, instance: "Store", key: "Ref", value: Any, /) -> None:
        self.records.append(GetitemRecord(instance, key, value))

    def on_setitem(
        self,
        instance: "Store",
        key: "Ref",
        old_value: Union[Missing, Any],
        new_value: Any,
        /,
    ) -> None:
        self.records.append(SetitemRecord(instance, key, old_value, new_value))

    def on_delitem(
        self, instance: "Store", key: "Ref", old_value: Union[Missing, Any], /
    ) -> None:
        self.records.append(DelitemRecord(instance, key, old_value))
