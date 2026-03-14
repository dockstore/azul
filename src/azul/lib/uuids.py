from hashlib import (
    sha1,
)
from itertools import (
    accumulate,
)
import math
from typing import (
    Any,
    ClassVar,
    Self,
    dataclass_transform,
)
from uuid import (
    UUID,
)

from attrs import (
    frozen,
)

from azul import (
    cached_property,
)
from azul.lib import (
    R,
)
from azul.lib.json import (
    Serializable,
)
from azul.lib.types import (
    AnyJSON,
    MutableJSON,
    json_int,
    json_mapping,
)


class InvalidUUIDError(Exception):

    def __init__(self, uuid: str, *args):
        super().__init__(f'{uuid!r} is not a valid UUID.', *args)


class InvalidUUIDVersionError(InvalidUUIDError):

    def __init__(self, uuid: UUID):
        super().__init__(str(uuid), f'Not a valid RFC-4122 UUID (undefined version {uuid.version}).')


class InvalidUUIDPrefixError(Exception):

    def __init__(self, prefix: str):
        super().__init__(f'{prefix!r} is not a valid UUID prefix.')


def validate_uuid(uuid_str: str) -> None:
    """
    >>> validate_uuid('8f53d355-b2fa-4bab-a2f2-6852d852d2ec')

    >>> validate_uuid('foo')
    Traceback (most recent call last):
    ...
    azul.lib.uuids.InvalidUUIDError: 'foo' is not a valid UUID.

    >>> validate_uuid('8F53d355-b2fa-4bab-a2f2-6852d852d2ec')
    Traceback (most recent call last):
    ...
    azul.lib.uuids.InvalidUUIDError: '8F53d355-b2fa-4bab-a2f2-6852d852d2ec' is not a valid UUID.

    >>> validate_uuid('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa') # doctest: +NORMALIZE_WHITESPACE
    Traceback (most recent call last):
    ...
    azul.lib.uuids.InvalidUUIDVersionError: ("'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' is not a valid UUID.",
    'Not a valid RFC-4122 UUID (undefined version 10).')
    """
    try:
        formatted_uuid = UUID(uuid_str)
    except ValueError:
        raise InvalidUUIDError(uuid_str)
    else:
        if str(formatted_uuid) != uuid_str:
            raise InvalidUUIDError(uuid_str)
        if formatted_uuid.version not in (1, 3, 4, 5):
            raise InvalidUUIDVersionError(formatted_uuid)


def validate_uuid_prefix(uuid_prefix: str) -> None:
    """
    # The empty string is a valid prefix
    >>> validate_uuid_prefix('')

    >>> validate_uuid_prefix('8f53')

    # A complete UUID is a valid prefix
    >>> validate_uuid_prefix('8f53d355-b2fa-4bab-a2f2-6852d852d2ec')

    >>> validate_uuid_prefix('8F53')
    Traceback (most recent call last):
    ...
    azul.lib.uuids.InvalidUUIDPrefixError: '8F53' is not a valid UUID prefix.

    >>> validate_uuid_prefix('8')

    >>> validate_uuid_prefix('8f538f53')

    >>> validate_uuid_prefix('8f538f5-')
    Traceback (most recent call last):
    ...
    AssertionError: R('UUID prefix ends with an invalid character', '8f538f5-')

    >>> validate_uuid_prefix('8f538f-')
    Traceback (most recent call last):
    ...
    AssertionError: R('UUID prefix ends with an invalid character', '8f538f-')

    >>> validate_uuid_prefix('8f538f53a')
    Traceback (most recent call last):
    ...
    azul.lib.uuids.InvalidUUIDPrefixError: '8f538f53a' is not a valid UUID prefix.
    """
    valid_uuid_str = '26a8fccd-bbd2-4342-9c19-6ed7c9bb9278'
    assert not uuid_prefix.endswith('-'), R(
        'UUID prefix ends with an invalid character', uuid_prefix)
    try:
        validate_uuid(uuid_prefix + valid_uuid_str[len(uuid_prefix):])
    except InvalidUUIDError:
        raise InvalidUUIDPrefixError(uuid_prefix)


def change_version(uuid: str, old_version: int, new_version: int) -> str:
    """
    >>> change_version('d36eb64f-162c-4b8f-bb17-069e2fd2b208', 1, 10)
    Traceback (most recent call last):
    ...
    AssertionError: ('d36eb64f-162c-4b8f-bb17-069e2fd2b208', 4, 1)
    >>> change_version('d36eb64f-162c-4b8f-bb17-069e2fd2b208', 4, 10)
    'd36eb64f-162c-ab8f-bb17-069e2fd2b208'
    """
    assert 1 <= new_version < 16, new_version
    if old_version in (1, 3, 4, 5):
        validate_uuid(uuid)
    prefix, version, suffix = uuid[:14], uuid[14], uuid[15:]
    version = int(version, 16)
    assert version == old_version, (uuid, version, old_version)
    uuid = f'{prefix}{new_version:x}{suffix}'
    assert UUID(uuid).version == new_version, (uuid, old_version)
    if new_version in (1, 3, 4, 5):
        validate_uuid(uuid)
    return uuid


@dataclass_transform(frozen_default=True,
                     kw_only_default=True,
                     order_default=True)
class UUIDPartitionMeta(type):

    def __init__(cls, name: str, bases: tuple[type, ...], members: dict[str, Any]):
        super().__init__(name, bases, members)

        # We can't use slots=True for two reasons:
        #
        # 1) slots=True causes attrs to duplicate the class (the instance of
        #    this metaclass), which then causes this method to be invoked twice,
        #    the second time feeding an already decorated class back to attrs.
        #    This could be addressed by overriding __new__ instead of __init__.
        #
        # 2) We would like to be able to use @cached_property on methods of
        #    instances, and @cached_property does not work with slotted classes.
        #
        # The assert below ensures that attrs does not duplicate the class, and
        # instead only augments it.
        #
        assert cls is frozen(kw_only=True, slots=False, order=True)(cls)
        cls.root = cls(prefix_length=0, prefix=0)


class UUIDPartition(Serializable, metaclass=UUIDPartitionMeta):
    """
    A binary partitioning of the UUID space. Most partitionings of the UUID
    space use a prefix of the hexadecimal representation of UUIDs. This class
    uses the binary representation and is therefore more granular.
    """
    #: The number of high-order bits of the binary representation of a UUID that
    #: have to be equal to the prefix for a UUID to be part of this partion.
    #:
    prefix_length: int

    #: The prefix. Only the `prefix_length` low-order bits are compared. The
    #: remaining high-order bits have to be 0.
    #:
    prefix: int

    #: The canonical string representation of UUIDs has five groups of
    #: hexadecimal digits separated by dash. The first group is eight digits
    #: long, the last group twelve and the three groups in between are four
    #: digits long. The first and the last group are best suited for a random
    #: distribution of v4 v5 UUIDs across partitions. By default, UUID
    #: partitions use the first group.
    #:
    group: int = 0

    #: The partition that includes all UUIDs. Since this attribute holds an
    #: instance of this class, we can't initialize it here, but have to do so in
    #: the metaclass constructor.
    #:
    root: ClassVar[Self]

    #: The width of each group in bits.
    #:
    group_lengths: ClassVar[tuple[int, ...]]
    group_lengths = tuple(4 * n for n in [8, 4, 4, 4, 12])

    #: For each group, the number of bits to right-shift the binary, 128-bit-
    #: wide representation of a UUID in order to have the bits of that group
    #: become the low-order bits.
    #:
    group_shifts: ClassVar[tuple[int, ...]]
    group_shifts = tuple(accumulate(group_lengths[:-1], initial=0))

    def __attrs_post_init__(self):
        """
        >>> UUIDPartition(prefix_length=0, prefix=1)
        ... # doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
        ...
        AssertionError: R('If prefix length is 0, the prefix must be, too',
        UUIDPartition(prefix_length=0, prefix=1, group=0))

        >>> UUIDPartition(prefix_length=1, prefix=3)
        ... # doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
        ...
        AssertionError: R('Prefix has extra high-order bits set',
        UUIDPartition(prefix_length=1, prefix=3, group=0))

        >>> UUIDPartition(prefix_length=1, prefix=0, group=5)
        ... # doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
        ...
        AssertionError: R('Invalid group',
        UUIDPartition(prefix_length=1, prefix=0, group=5))

        >>> UUIDPartition(prefix_length=1, prefix=0, group=-1)
        ... # doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
        ...
        AssertionError: R('Invalid group',
        UUIDPartition(prefix_length=1, prefix=0, group=-1))

        >>> UUIDPartition(prefix_length=49, prefix=0, group=4)
        Traceback (most recent call last):
        ...
        AssertionError: R('Length of prefix exceeds that of group', 49, 48)

        >>> UUIDPartition(prefix_length=17, prefix=0, group=1)
        Traceback (most recent call last):
        ...
        AssertionError: R('Length of prefix exceeds that of group', 17, 16)
        """
        assert self.prefix_length != 0 or self.prefix == 0, R(
            'If prefix length is 0, the prefix must be, too', self)
        assert 0 <= self.group < len(self.group_shifts), R(
            'Invalid group', self)
        group_length = self.group_lengths[self.group]
        assert self.prefix_length <= group_length, R(
            'Length of prefix exceeds that of group', self.prefix_length, group_length)
        assert 0 <= self.prefix < 2 ** self.prefix_length, R(
            'Prefix has extra high-order bits set', self)

    @classmethod
    def from_json(cls, json: AnyJSON) -> Self:
        m = json_mapping(json)
        return cls(prefix_length=json_int(m['prefix_length']),
                   prefix=json_int(m['prefix']),
                   group=json_int(m['group']))

    def to_json(self) -> MutableJSON:
        return {
            'prefix_length': self.prefix_length,
            'prefix': self.prefix,
            'group': self.group
        }

    def contains(self, member: UUID) -> bool:
        """
        >>> p = UUIDPartition(prefix_length=7, prefix=0b1111_111)
        >>> p.contains(UUID('fdd4524e-14c4-41d7-9071-6cadab09d75c'))
        False
        >>> p.contains(UUID('fed4524e-14c4-41d7-9071-6cadab09d75c'))
        True
        >>> p.contains(UUID('ffd4524e-14c4-41d7-9071-6cadab09d75c'))
        True

        >>> p = UUIDPartition(prefix_length=5, prefix=0b0110_0, group=4)
        >>> p.contains(UUID('fdd4524e-14c4-41d7-9071-66adab09d75c'))
        True
        >>> p.contains(UUID('fdd4524e-14c4-41d7-9071-67adab09d75c'))
        True
        >>> p.contains(UUID('fdd4524e-14c4-41d7-9071-68adab09d75c'))
        False

        >>> p = UUIDPartition(prefix_length=48, prefix=0x68adab09d75c, group=4)
        >>> p.contains(UUID('fdd4524e-14c4-41d7-9071-68adab09d75c'))
        True
        >>> p.contains(UUID('fdd4524e-14c4-41d7-9071-68adab09d75d'))
        False
        """
        mask, shift = self._mask_and_shift
        return (member.int & mask) >> shift == self.prefix

    @cached_property
    def _mask_and_shift(self) -> tuple[int, int]:
        group_shift = self.group_shifts[self.group]
        shift = 128 - self.prefix_length - group_shift
        mask = (1 << (128 - group_shift)) - 1
        return mask, shift

    def divide(self, num_divisions: int) -> list[Self]:
        """
        Divide this partition into a set of at least the given number of
        sub-partitions. The length of the return value will always be the
        smallest a power of two that is greater than ``num_divisions`.

        >>> UUIDPartition.root.divide(0)
        Traceback (most recent call last):
        ...
        AssertionError: R('Number of divisions must be 1 or more')

        >>> UUIDPartition.root.divide(1) == [UUIDPartition.root]
        True

        >>> sorted(UUIDPartition.root.divide(3))
        ... # doctest: +NORMALIZE_WHITESPACE
        [UUIDPartition(prefix_length=2, prefix=0, group=0),
        UUIDPartition(prefix_length=2, prefix=1, group=0),
        UUIDPartition(prefix_length=2, prefix=2, group=0),
        UUIDPartition(prefix_length=2, prefix=3, group=0)]

        >>> UUIDPartition(prefix_length=2, prefix=0, group=4).divide(2)
        ... # doctest: +NORMALIZE_WHITESPACE
        [UUIDPartition(prefix_length=3, prefix=0, group=4),
        UUIDPartition(prefix_length=3, prefix=1, group=4)]
        """
        assert num_divisions > 0, R('Number of divisions must be 1 or more')
        prefix_length = math.ceil(math.log2(num_divisions))
        num_divisions = 2 ** prefix_length
        cls = type(self)
        return [
            cls(prefix_length=self.prefix_length + prefix_length,
                prefix=(self.prefix << prefix_length) + prefix,
                group=self.group)
            for prefix in range(num_divisions)
        ]

    def __str__(self) -> str:
        """
        Represent this partition as a hexadecimal range. This range can be used
        to visually tell wether this partition contains a particular UUID: it
        does, if the UUID starts with any hexadecimal sequence in the range
        returned by this function.

        >>> str(UUIDPartition.root)
        '-@0'

                                                      0b1111_1110 == 0xfe
                                                      0b1111_1111 == 0xff
        >>> str(UUIDPartition(prefix_length=7, prefix=0b1111_111, group=4))
        'fe-ff@4'

        Leading zeroes in the high and low end of the range:

                                                      0b0000_1110 == 0x0e
                                                      0b0000_1111 == 0x0f
        >>> str(UUIDPartition(prefix_length=7, prefix=0b0000_111, group=4))
        '0e-0f@4'

        A partition twice as big (a binary prefix that's one bit shorter):

                                                      0b0000_1100 = 0x0c
                                                      0b0000_1101 = 0x0d
                                                      0b0000_1110 = 0x0e
                                                      0b0000_1111 = 0x0f
        >>> str(UUIDPartition(prefix_length=6, prefix=0b0000_11, group=4))
        '0c-0f@4'
        """
        shift = 4 - self.prefix_length % 4  # shift to align at nibble boundary
        all_ones = (1 << shift) - 1
        lo = self.prefix << shift
        hi = lo + all_ones

        hex_len = (self.prefix_length + 3) // 4

        def hex(i):
            return format(i, f'0{hex_len}x')[:hex_len]

        return f'{hex(lo)}-{hex(hi)}@{self.group}'


def uuid5_for_bytes(namespace: UUID, name: bytes) -> UUID:
    """
    Generate a UUID from the SHA-1 hash of a namespace UUID and a name. Same as
    uuid.uuid5 but takes `bytes` not `str`, and thereby avoids assuming an
    encoding (uuid.uuid5 assumes UTF-8).
    """
    hash = sha1(namespace.bytes + name).digest()
    return UUID(bytes=hash[:16], version=5)
