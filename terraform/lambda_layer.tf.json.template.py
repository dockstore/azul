from azul import (
    config,
)
from azul.deployment import (
    aws,
)
from azul.infra.terraform import (
    emit_tf,
)
from azul.lambda_layer import (
    DependenciesLayer,
)

layer = DependenciesLayer()

emit_tf({
    "resource": [
        {
            "aws_lambda_layer_version": {
                "dependencies": {
                    "layer_name": config.qualified_resource_name("dependencies"),
                    "s3_bucket": aws.shared_bucket,
                    "s3_key": layer.object_key
                }
            }
        }
    ],
})
