from azul.lib import (
    R,
)
from azul.logging import (
    configure_script_logging,
)
from azul.opensearch import (
    OpenSearchClientFactory,
)

configure_script_logging()


def main():
    es = OpenSearchClientFactory.get()
    response = es.cluster.put_settings(body={
        'persistent': {
            'action.auto_create_index': False
        }
    })
    assert response['acknowledged'], R('Failed to update cluster settings', response)


if __name__ == '__main__':
    main()
