from azul import (
    R,
)
from azul.deployment import (
    aws,
)
from azul_test_case import (
    AzulUnitTestCase,
)


class TestDeploymentAWS(AzulUnitTestCase):

    def test_qualified_bucket_name(self):
        self.assertEqual(f'edu-ucsc-gi-{self._aws_account_name}-foo.us-gov-west-1',
                         aws.qualified_bucket_name('foo'))
        for invalid in ['', 'x', '1foo']:
            with self.subTest(invalid=invalid):
                with self.assertRaises(AssertionError) as cm:
                    aws.qualified_bucket_name(invalid)
                self.assertTrue(R.caused(cm.exception))
