import boto3

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
        pass
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
