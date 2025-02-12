import json

import boto3
from more_itertools import (
    one,
)

from azul import (
    config,
    logging,
)
from azul.logging import (
    configure_script_logging,
)
from azul.terraform import (
    terraform,
)

log = logging.getLogger(__name__)


def main():
    tf_component = config.terraform_component
    if tf_component == '':
        import_log_groups(lambda_function_log_groups())
    elif tf_component in ('browser', 'gitlab'):
        pass
    elif tf_component == 'shared':
        if config.slack_integration:
            import_log_groups(chatbot_log_groups())
    else:
        assert False


def log_group_name(name: str) -> str:
    return 'aws_cloudwatch_log_group.' + name


def chatbot_log_groups() -> dict[str, str]:
    name = log_group_name('chatbot')
    log_group = '/aws/chatbot/' + config.qualified_resource_name('chatbot')
    return {name: log_group}


def lambda_function_log_groups() -> dict[str, str]:
    tf_path = config.project_root + '/terraform/api_gateway.tf.json'
    with open(tf_path, 'r') as f:
        tf_json = json.load(f)
    log_groups = {}
    for resources_by_type in tf_json['resource']:
        resource_type, resources = one(resources_by_type.items())
        if resource_type == 'aws_lambda_function':
            for resource in resources:
                for resource_name, resource_def in resource.items():
                    name = log_group_name(resource_name + '_lambda')
                    log_group = '/aws/lambda/' + resource_def['function_name']
                    log_groups[name] = log_group
    return log_groups


def import_log_groups(log_groups: dict[str, str]) -> None:
    log_client = boto3.client('logs')
    paginator = log_client.get_paginator('describe_log_groups')
    existing_log_groups = {
        log_group['logGroupName']
        for page in paginator.paginate()
        for log_group in page['logGroups']
    }
    resources = terraform.run('state', 'list').splitlines()
    for resource_name, log_group in log_groups.items():
        if resource_name in resources:
            log.info('Skipping import of %r, resource already imported', resource_name)
        elif log_group not in existing_log_groups:
            log.info('Skipping import of %r, resource does not exist', resource_name)
        else:
            log.info('Importing resource %r', resource_name)
            terraform.run('import', resource_name, log_group)


if __name__ == '__main__':
    configure_script_logging()
    main()
