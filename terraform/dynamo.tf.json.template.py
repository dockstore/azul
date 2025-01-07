from azul import (
    config,
)
from azul.service.source_service import (
    SourceService,
)
from azul.terraform import (
    emit_tf,
)

emit_tf(
    {
        "resource": [
            {
                "aws_dynamodb_table": {
                    "sources_cache_by_auth": {
                        "name": config.dynamo_sources_cache_table_name,
                        "billing_mode": "PAY_PER_REQUEST",
                        "hash_key": SourceService.key_attribute,
                        "attribute": [
                            {
                                "name": SourceService.key_attribute,
                                "type": "S"
                            }
                        ],
                        "ttl": {
                            "attribute_name": SourceService.ttl_attribute,
                            "enabled": True
                        }
                    }
                }
            }
        ]
    }
)
