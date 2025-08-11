from abc import (
    ABCMeta,
    abstractmethod,
)
from collections import (
    Counter,
    defaultdict,
)
import logging
from typing import (
    Any,
    Callable,
)

from azul import (
    R,
)
from azul.collections import (
    none_safe_key,
)
from azul.indexer.document import (
    EntityType,
)
from azul.json_freeze import (
    freeze,
    thaw,
)
from azul.types import (
    JSON,
    JSONs,
)

log = logging.getLogger(__name__)


class Accumulator(metaclass=ABCMeta):
    """
    Accumulates multiple values into a single value, not necessarily of the same
    type.
    """

    def __init__(self):
        self.dropped = 0

    @abstractmethod
    def accumulate(self, value):
        """
        Incorporate the given value into this accumulator. If the value is not
        incorporated (due to e.g. a maximum size constraint), implementations
        should increment :py:attr:`dropped`.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self):
        """
        Return the accumulated value.
        """
        raise NotImplementedError


class SumAccumulator(Accumulator):
    """
    Add values.

    Unlike the sum() built-in, this accumulator doesn't default to an initial
    value of 0 but defaults to the first accumulated value instead.
    """

    def __init__(self, *, initially=None) -> None:
        """
        :param initially: the initial value for the sum. If None, the first
                          accumulated value that is not None will be used to
                          initialize the sum. Note that if this parameter is
                          None, the return value of close() could be None, too.
        """
        super().__init__()
        self.value = initially

    def accumulate(self, value) -> None:
        if value is not None:
            if self.value is None:
                self.value = value
            else:
                self.value += value

    def get(self):
        return self.value


class SetAccumulator(Accumulator):
    """
    Accumulates values into a set, discarding duplicates and, optionally, values
    that would grow the set past the maximum size. The accumulated value is
    returned as a sorted list. The maximum size constraint does not take the
    ordering into account. This accumulator does not return a list of the N
    smallest values, it returns a sorted list of the first N distinct values.
    """

    def __init__(self, max_size=None, key=None) -> None:
        """
        :param max_size: the maximum number of elements to retain

        :param key: The key to be used for sorting the accumulated set of
                    values. If this value is None, a default None-safe key will
                    be used. With that default key, if any None values were
                    placed in the accumulator, the first element, and only the
                    first element of the returned list will be None.
        """
        super().__init__()
        self.value = set()
        self.max_size = max_size
        self.key = none_safe_key(none_last=True) if key is None else key

    def accumulate(self, value) -> int:
        """
        :return: The number of values that were incorporated. There are two
                 reasons a value may not be incorporated: it was already in the
                 set or the accumulator is full. The latter is reflected in
                 self.dropped

        >>> acc = SetAccumulator(max_size=4)
        >>> acc.accumulate(1), acc.get(), acc.dropped
        (1, [1], 0)

        >>> acc.accumulate(1), acc.get(), acc.dropped
        (0, [1], 0)

        >>> acc.accumulate(2), acc.get(), acc.dropped
        (1, [1, 2], 0)

        >>> acc.accumulate([1, 2, 3]), acc.get(), acc.dropped
        (1, [1, 2, 3], 0)

        >>> acc.accumulate([1, 2, 3]), acc.get(), acc.dropped
        (0, [1, 2, 3], 0)

        >>> acc.accumulate([3, 4, 5]), acc.get(), acc.dropped
        (1, [1, 2, 3, 4], 1)

        >>> acc.accumulate([5, 6]), acc.get(), acc.dropped
        (0, [1, 2, 3, 4], 3)
        """
        # Tuples are treated as scalars. We rely on this behavior when
        # aggregating `ValueAndUnit` fields.
        if not isinstance(value, list):
            value = [value]
        initial_len = len(self.value)
        assert self.max_size is None or initial_len <= self.max_size, (
            self.value, self.max_size)
        if self.max_size is None or len(value) + initial_len <= self.max_size:
            self.value.update(value)
        elif initial_len == self.max_size:
            self.dropped += len(value)
        else:
            for v in value:
                if len(self.value) < self.max_size:
                    self.value.add(v)
                elif v not in self.value:
                    self.dropped += 1
        return len(self.value) - initial_len

    def get(self) -> list[Any]:
        return sorted(self.value, key=self.key)


class SetOfDictAccumulator(SetAccumulator):
    """
    A set accumulator that supports mutable mappings as values.

    >>> acc = SetOfDictAccumulator(key=lambda d: d['foo'])
    >>> d = {'foo': 2}
    >>> acc.accumulate(d)
    1

    >>> acc.accumulate(d)
    0

    >>> d = {'foo': 1, 'bar': 1}
    >>> acc.accumulate(d)
    1

    >>> acc.accumulate([d, d])
    0

    >>> acc.get()
    [{'foo': 1, 'bar': 1}, {'foo': 2}]
    """

    def accumulate(self, value) -> int:
        if isinstance(value, list):
            # `freeze` converts lists to tuples, which the superclass treats as
            # scalars instead of sequences. Passing a list as a tuple would
            # therefore introduce an extraneous level of nesting, as every
            # element in `value` would end up in a single element of the
            # accumulated result.
            frozen_value = list(map(freeze, value))
        else:
            frozen_value = freeze(value)
        return super().accumulate(frozen_value)

    def get(self):
        return thaw(super().get())


class DictAccumulator(Accumulator):
    """
    Accumulate values into a dictionary, allowing one unique value per key,
    discarding values that would exceed the maximum number of dictionary keys.
    In a way this is a generalized SetAccumulator. DictAccumulator can replace a
    SetAccumulator by using the identity function for the key.
    """

    def __init__(self, max_size: int | None, key: Callable):
        """
        :param max_size: The maximum number of elements to retain. A value of
                         None can be used to specify no maximum.

        :param key: A function returning the key to be used both for storing the
                    accumulated value and sorting the accumulated set of values.
        """
        super().__init__()
        self.max_size = max_size
        self.key = key
        self.value = {}

    def accumulate(self, value):
        """
        >>> acc = DictAccumulator(max_size=3, key=lambda s: s.lower())
        >>> acc.accumulate('foo')
        >>> acc.get(), acc.dropped
        (['foo'], 0)

        >>> acc.accumulate('foo')
        >>> acc.get(), acc.dropped
        (['foo'], 0)

        >>> acc.accumulate('Foo')
        Traceback (most recent call last):
        ...
        AssertionError: R('Ambiguos key:', 'foo', 'values:', 'foo', 'Foo')

        >>> acc.accumulate('Bar')
        >>> acc.accumulate('BAZ')
        >>> acc.get(), acc.dropped
        (['Bar', 'BAZ', 'foo'], 0)

        >>> acc.accumulate('spam')
        >>> acc.get(), acc.dropped
        (['Bar', 'BAZ', 'foo'], 1)
        """
        key = self.key(value)
        if self.max_size is None or len(self.value) < self.max_size:
            try:
                old_value = self.value[key]
            except KeyError:
                self.value[key] = value
            else:
                assert old_value == value, R(
                    'Ambiguos key:', key, 'values:', old_value, value)
        elif key not in self.value:
            self.dropped += 1

    def get(self):
        return sorted(self.value.values(), key=self.key)


class FrequencySetAccumulator(Accumulator):
    """
    An accumulator that accepts any number of values and returns a list with
    at most max_size most frequently occurring values.

    Note the max_size argument only limits the length of the accumulate, the
    overall menory consumption of this accumulator is unbounded.

    >>> acc = FrequencySetAccumulator(2)
    >>> acc.accumulate('x')
    >>> acc.accumulate(['x','y'])
    >>> acc.accumulate(['x','y','z'])
    >>> acc.get()
    ['x', 'y']

    >>> acc = FrequencySetAccumulator(0)
    >>> acc.accumulate('x')
    >>> acc.get()
    []
    """

    def __init__(self, max_size) -> None:
        super().__init__()
        self.value = Counter()
        self.max_size = max_size

    def accumulate(self, value) -> None:
        if isinstance(value, (dict, list)):
            self.value.update(value)
        else:
            self.value[value] += 1

    def get(self) -> list[Any]:
        self.dropped = max(0, len(self.value) - self.max_size)
        return [item for item, count in self.value.most_common(self.max_size)]


class LastValueAccumulator(Accumulator):
    """
    An accumulator that accepts any number of values and returns the value most
    recently seen.
    """

    def __init__(self) -> None:
        super().__init__()
        self.value = None

    def accumulate(self, value):
        self.value = value

    def get(self):
        return self.value


class SingleValueAccumulator(LastValueAccumulator):
    """
    An accumulator that accepts any number of values given that they all are the
    same value and returns a single value. Occurrence of any value that is
    different than the first accumulated value raises a ValueError.
    """

    def accumulate(self, value):
        if self.value is None:
            super().accumulate(value)
        elif self.value != value:
            raise ValueError('Conflicting values:', self.value, value)


class MinAccumulator(LastValueAccumulator):
    """
    An accumulator that returns the minimal value seen.
    """

    def accumulate(self, value):
        if value is not None and (self.value is None or value < self.value):
            super().accumulate(value)


class MaxAccumulator(LastValueAccumulator):
    """
    An accumulator that returns the maximal value seen.
    """

    def accumulate(self, value):
        if value is not None and (self.value is None or value > self.value):
            super().accumulate(value)


class DistinctAccumulator(Accumulator):
    """
    An accumulator for (key, value) tuples. Of two pairs with the same key, only
    the value from the first pair will be accumulated. The actual values will be
    accumulated in another accumulator instance specified at construction.

    >>> acc = DistinctAccumulator(SumAccumulator(initially=0), max_size=3)

    Keys can be tuples, too.

    >>> acc.accumulate((('x', 'y'), 3))

    Values associated with a recurring key will not be accumulated.

    >>> acc.accumulate((('x', 'y'), 4))
    >>> acc.accumulate(('a', 20))
    >>> acc.accumulate(('b', 100))

    Accumulation stops at max_size distinct keys.

    >>> acc.accumulate(('c', 1000))
    >>> acc.get()
    123
    """

    def __init__(self, inner: Accumulator, max_size: int = None) -> None:
        super().__init__()
        self.value = inner
        self.keys = SetAccumulator(max_size=max_size)

    def accumulate(self, value):
        key, value = value
        if self.keys.accumulate(key):
            self.value.accumulate(value)

    def get(self):
        return self.value.get()


class UniqueValueCountAccumulator(SetAccumulator):
    """
    Count the number of unique values
    """

    def get(self) -> int:
        return len(super().get())


class EntityAggregator(metaclass=ABCMeta):

    def __init__(self, outer_entity_type: EntityType, entity_type: EntityType):
        self.outer_entity_type = outer_entity_type
        self.entity_type = entity_type

    def _transform_entity(self, entity: JSON) -> JSON:
        return entity

    def _accumulator(self, field: str) -> Accumulator | None:
        """
        Return the Accumulator instance to be used for the given field or None
        if the field should not be accumulated.
        """
        return self._default_accumulator()

    def _default_accumulator(self) -> Accumulator | None:
        return SetAccumulator(max_size=100)

    @abstractmethod
    def aggregate(self, entities: JSONs) -> JSONs:
        raise NotImplementedError


class SimpleAggregator(EntityAggregator):

    def aggregate(self, entities: JSONs) -> JSONs:
        aggregate = {}
        for entity in entities:
            self._accumulate(aggregate, entity)
        return [self._aggregate(aggregate)] if aggregate else []

    def _accumulate(self, aggregate: dict[str, Accumulator | None], entity: JSON):
        entity = self._transform_entity(entity)
        for field, value in entity.items():
            try:
                accumulator = aggregate[field]
            except KeyError:
                accumulator = self._accumulator(field)
                aggregate[field] = accumulator
            if accumulator is not None:
                accumulator.accumulate(value)

    def _aggregate(self, aggregate: dict[str, Accumulator]) -> JSON:
        result = {}
        for k, accumulator in aggregate.items():
            if accumulator is not None:
                result[k] = accumulator.get()
                if accumulator.dropped > 0:
                    log.warning('Values were dropped %d times while aggregating %s.%s into %s',
                                accumulator.dropped, self.entity_type, k, self.outer_entity_type)
        return result


class GroupingAggregator(SimpleAggregator):

    def aggregate(self, entities: JSONs) -> JSONs:
        aggregates: dict[Any, dict[str, Accumulator | None]] = defaultdict(dict)
        for entity in entities:
            group_keys = self._group_keys(entity)
            aggregate = aggregates[group_keys]
            self._accumulate(aggregate, entity)
        return [
            self._aggregate(aggregate)
            for aggregate in aggregates.values()
        ]

    @abstractmethod
    def _group_keys(self, entity) -> tuple[Any, ...]:
        raise NotImplementedError
