from azul import (
    config,
)
from azul.deployment import (
    aws,
)
from azul.terraform import (
    block_public_s3_bucket_access,
    emit_tf,
    enable_s3_bucket_inventory,
    set_empty_s3_bucket_lifecycle_config,
)

tf_config = {
    'data': {
        'aws_s3_bucket': {
            config.logs_term: {
                'bucket': aws.logs_bucket,
            }
        },
    },
    'resource': {
        'aws_s3_bucket': {
            config.storage_term: {
                'bucket': aws.storage_bucket,
                'force_destroy': True
            }
        },
        'aws_s3_bucket_lifecycle_configuration': {
            config.storage_term: {
                'bucket': '${aws_s3_bucket.%s.id}' % config.storage_term,
                'rule': {
                    'id': 'manifests',
                    'status': 'Enabled',
                    'filter': {
                        'prefix': 'manifests/'
                    },
                    'expiration': {
                        'days': config.manifest_expiration
                    },
                    'abort_incomplete_multipart_upload': {
                        'days_after_initiation': 1
                    }
                }
            }
        },
        'aws_s3_bucket_logging': {
            config.storage_term: {
                'bucket': '${aws_s3_bucket.%s.id}' % config.storage_term,
                'target_bucket': '${data.aws_s3_bucket.%s.id}' % config.logs_term,
                # Other S3 log deliveries, like ELB, implicitly put a slash
                # after the prefix. S3 doesn't, so we add one explicitly.
                'target_prefix': config.s3_access_log_path_prefix(config.storage_term) + '/'
            }
        }
    }
}
tf_config = enable_s3_bucket_inventory(tf_config)
tf_config = block_public_s3_bucket_access(tf_config)
tf_config = set_empty_s3_bucket_lifecycle_config(tf_config)
emit_tf(tf_config)
