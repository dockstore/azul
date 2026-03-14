# Most of the [de]serialization code is covered by doctests instead. However,
# the functionality in DynamicRegisteredPolymorphicSerializable requires that
# its subclasses are top-level members of a module, and thus requires its own
# module for testing.


import attrs

from azul.json import (
    DynamicPolymorphicSerializable,
)
from azul.lib.attrs import (
    DiscriminatingPolymorphicSerializableAttrs,
)
from azul_test_case import (
    AzulUnitTestCase,
)


@attrs.frozen(kw_only=True)
class A(DiscriminatingPolymorphicSerializableAttrs,
        DynamicPolymorphicSerializable):
    a: int

    @classmethod
    def discriminator(cls) -> str:
        return 'type'


@attrs.frozen(kw_only=True)
class B(A):
    b: str


@attrs.frozen(kw_only=True)
class C(A):
    c: float
    nested_a: A


class TestPolymorphicSerialization(AzulUnitTestCase):

    def test(self):
        a = A(a=1)
        b = B(a=1, b='2')
        c = C(a=1, c=2.3, nested_a=b)

        a_json = a.to_json()
        self.assertEqual(a_json, {
            'a': 1,
            'type': 'test_json.A'
        })

        b_json = b.to_json()
        self.assertEqual(b_json, {
            'a': 1,
            'b': '2',
            'type': 'test_json.B'
        })

        c_json = c.to_json()
        self.assertEqual(c_json, {
            'a': 1,
            'c': 2.3,
            'nested_a': {
                'a': 1,
                'b': '2',
                'type': 'test_json.B'
            },
            'type': 'test_json.C'
        })

        self.assertEqual(A.from_json(a_json), a)
        self.assertEqual(B.from_json(b_json), b)
        self.assertEqual(C.from_json(c_json), c)

        self.assertEqual(A.from_json(b_json), b)
        self.assertEqual(A.from_json(c_json), c)

        # The discriminator is optional when from_json is invoked
        # through a concrete subclass
        b_json.pop('type')
        self.assertEqual(B.from_json(b_json), b)

        c_json.pop('type')
        self.assertEqual(C.from_json(c_json), c)
