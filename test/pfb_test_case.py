import json
from pathlib import (
    Path,
)

import fastavro

from indexer import (
    CannedFileTestCase,
)


class PFBTestCase(CannedFileTestCase):

    def _assert_pfb_schema(self, schema):
        fastavro.parse_schema(schema)
        # Parsing successfully proves our schema is valid
        with self.assertRaises(KeyError):
            fastavro.parse_schema({'this': 'is not', 'an': 'avro schema'})

        actual = json.dumps(schema, indent=4, sort_keys=True)
        expected = self._data_path('service', 'manifest', 'terra', 'pfb_manifest.schema.json')
        self._assert_or_create_json_can(expected, actual)

    def _assert_or_create_json_can(self, expected: Path, actual: str):
        if expected.exists():
            with open(expected, 'r') as f:
                expected = json.load(f)
            self.assertEqual(expected, json.loads(actual))
        else:
            with open(expected, 'w') as f:
                f.write(actual)
