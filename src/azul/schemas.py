import json
from typing import (
    Any,
)

from azul import (
    JSON,
    format_description as fd,
    mutable_furl,
)
from azul.chalice import (
    AppController,
)
from azul.openapi import (
    params,
    responses,
    schema,
)


class SchemaController(AppController):
    """
    A controller for serving JSON schemas relating to an Azul facility
    """
    schema_url_path = '/schemas/{facility}/{schema_name}/{version_and_extension}'

    def schema_url(self,
                   *,
                   facility: str,
                   schema_name: str,
                   version: int
                   ) -> mutable_furl:
        path = self.schema_url_path.format(facility=facility,
                                           schema_name=schema_name,
                                           version_and_extension=f'v{version}.json')
        return self.app.base_url.set(path=path)

    def handlers(self) -> dict[str, Any]:
        """
        Chalice routes and application handlers to be injected into the global
        scope of a Chalice application module.
        """

        @self.app.route(
            self.schema_url_path,
            methods=['GET'],
            cors=True,
            spec={
                'summary': 'Retrieve JSON schemas',
                'tags': ['Auxiliary'],
                'parameters': [
                    params.path('facility', str),
                    params.path('schema_name', str),
                    params.path('version_and_extension', schema.pattern(r'v\d+\.json')),
                ],
                'description': fd(
                    '''
                    [JSON Schemas](https://json-schema.org/docs) for various Azul facilities.
                    '''
                ),
                'responses': {
                    '200': {
                        'description': 'Contents of the schema',
                        **responses.json_content(
                            schema.object(
                                schema=str,
                                id=str,
                                type=str,
                                additionalProperties=True
                            )
                        )
                    }
                }
            }
        )
        def get_schema(facility: str,
                       schema_name: str,
                       version_and_extension: str
                       ) -> JSON:
            path = 'schemas', facility, schema_name, version_and_extension
            schema = json.loads(self.app.load_static_resource(*path))
            schema['$id'] = str(self.app.self_url)
            return schema

        return locals()
