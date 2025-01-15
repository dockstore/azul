from azul.openapi import (
    format_description_key,
    schema,
)
from azul.openapi.schema import (
    Form,
)
from azul.types import (
    JSON,
    PrimitiveJSON,
)


def path(name: str, form: Form, **kwargs: PrimitiveJSON) -> JSON:
    """
    Returns an OpenAPI `parameters` specification of a URL path parameter.
    Note that path parameters cannot be optional.

    >>> from azul.doctests import assert_json
    >>> assert_json(path('foo', int))
    {
        "name": "foo",
        "in": "path",
        "required": true,
        "schema": {
            "type": "integer",
            "format": "int64"
        }
    }
    """
    return _make_param(name, in_='path', form=form, **kwargs)


def query(name: str,
          form: Form | schema.optional,
          **kwargs: PrimitiveJSON
          ) -> JSON:
    """
    Returns an OpenAPI `parameters` specification of a URL query parameter.

    >>> from azul.doctests import assert_json
    >>> assert_json(query('foo', schema.optional(int)))
    {
        "name": "foo",
        "in": "query",
        "required": false,
        "schema": {
            "type": "integer",
            "format": "int64"
        }
    }
    """
    return _make_param(name, in_='query', form=form, **kwargs)


def header(name: str,
           form: Form | schema.optional,
           **kwargs: PrimitiveJSON
           ) -> JSON:
    """
    Returns an OpenAPI `parameters` specification of a request header.

    >>> from azul.doctests import assert_json
    >>> assert_json(header('X-foo', schema.optional(int)))
    {
        "name": "X-foo",
        "in": "header",
        "required": false,
        "schema": {
            "type": "integer",
            "format": "int64"
        }
    }
    """
    return _make_param(name, in_='header', form=form, **kwargs)


def _make_param(name: str,
                in_: str,
                form: Form | schema.optional,
                **kwargs: PrimitiveJSON
                ) -> JSON:
    if isinstance(form, schema.optional):
        form, required = form.form, False
    else:
        required = True
    format_description_key(kwargs)
    schema_or_content = schema.make_type(form)
    return {
        'name': name,
        'in': in_,
        'required': required,
        # https://swagger.io/docs/specification/describing-parameters/#schema-vs-content
        'content' if 'application/json' in schema_or_content else 'schema': schema_or_content,
        **kwargs
    }
