import argparse
import base64
import json
import logging
import os
import time
import uuid

import google.auth
import googleapiclient.discovery
from googleapiclient.errors import (
    HttpError,
)

from azul import (
    config,
)
from azul.deployment import (
    aws,
)
from azul.lib import (
    cached_property,
)
from azul.logging import (
    configure_script_logging,
)

log = logging.getLogger(__name__)


class CredentialsProvisioner:

    @cached_property
    def _google_iam(self):
        credentials, project_id = google.auth.default(
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        return googleapiclient.discovery.build('iam', 'v1', credentials=credentials)

    @property
    def _secrets_manager(self):
        return aws.secretsmanager

    def provision_sa(self, args):
        self._provision_sa(args.create, args.email, args.secret_name)

    def provision_hmac(self, args):
        self._provision_hmac(args.create)

    def _provision_sa(self, create, email, secret_name):
        secret_name = config.secrets_manager_secret_name(secret_name)
        if create:
            self._create_secret(secret_name)
            if not self._secret_is_stored(secret_name):
                google_key = self._create_sa_credentials(email)
                self._write_secret_value(secret_name, google_key)
        else:
            self._destroy_sa_credentials(email, secret_name)
            self._destroy_secret(secret_name)

    def _create_sa_credentials(self, email):
        iam = self._google_iam
        key_name = 'projects/-/serviceAccounts/' + email
        keys = iam.projects().serviceAccounts().keys()
        key = keys.create(name=key_name, body={}).execute()
        log.info('Successfully created service account key for user %r', email)
        return base64.decodebytes(bytes(key['privateKeyData'], 'ascii')).decode()

    def _destroy_sa_credentials(self, service_account_email, secret_name):
        try:
            creds = self._secrets_manager.get_secret_value(
                SecretId=config.secrets_manager_secret_name(secret_name)
            )
        except self._secrets_manager.exceptions.ResourceNotFoundException:
            log.info('Secret already deleted, cannot get key_id for %s',
                     service_account_email)
            return
        else:
            key_id = json.loads(creds['SecretString'])['private_key_id']
            iam = self._google_iam
            try:
                key_name = f'projects/-/serviceAccounts/{service_account_email}/keys/{key_id}'
                iam.projects().serviceAccounts().keys().delete(name=key_name).execute()
            except HttpError as e:
                if e.resp.reason != 'Not Found':
                    raise
            log.info('Successfully deleted service account key with id %r for user %r',
                     key_id, service_account_email)

    def _provision_hmac(self, create):
        secret_name = config.secrets_manager_secret_name('indexer', 'hmac')
        if create:
            self._create_secret(secret_name)
            if not self._secret_is_stored(secret_name):
                self._write_secret_value(secret_name, self._random_hmac_key())
        else:
            self._destroy_secret(secret_name)

    def _random_hmac_key(self):
        # Even though an HMAC key can be any sequence of bytes, we restrict to
        # base64 in order to encode as string
        key = base64.encodebytes(os.urandom(48)).decode().replace('=', '').replace('\n', '')
        assert len(key) == 64
        return json.dumps({'key': key, 'key_id': str(uuid.uuid4())})

    def _secret_is_stored(self, name):
        try:
            response = self._secrets_manager.get_secret_value(SecretId=name)
        except self._secrets_manager.exceptions.ResourceNotFoundException:
            return False
        try:
            return response['SecretString'] != ''
        except KeyError:
            return False

    def _create_secret(self, name):
        try:
            self._secrets_manager.create_secret(Name=name)
        except self._secrets_manager.exceptions.ResourceExistsException:
            log.info('AWS secret %s already exists.', name)
        else:
            log.info('AWS secret %s created.', name)

    def _write_secret_value(self, name, value):
        self._secrets_manager.put_secret_value(
            SecretId=name,
            SecretString=value
        )
        log.info('Successfully wrote value to AWS secret %r.', name)

    def _destroy_secret(self, name):
        try:
            response = self._secrets_manager.delete_secret(
                SecretId=name,
                ForceDeleteWithoutRecovery=True
            )
        except self._secrets_manager.exceptions.ResourceNotFoundException:
            log.info('AWS secret %s does not exist. No changes will be made.', name)
        else:
            assert response['Name'] == name
            # AWS docs recommend waiting for ResourceNotFoundException: "The
            # deletion is an asynchronous process. There might be a short delay"
            #
            # https://aws.amazon.com/premiumsupport/knowledge-center/delete-secrets-manager-secret/
            #
            deadline = time.time() + 60
            while True:
                try:
                    self._secrets_manager.describe_secret(SecretId=name)
                except self._secrets_manager.exceptions.ResourceNotFoundException:
                    log.info('Successfully deleted AWS secret %r.', name)
                    break
                else:
                    now = time.time()
                    if now >= deadline:
                        raise RuntimeError('Secret could not be destroyed', name)
                    else:
                        log.info('Secret %r not yet deleted. Will keep checking for %.3fs.',
                                 name, deadline - now)
                        time.sleep(5)


if __name__ == '__main__':
    # Suppress noisy warning from Google library. See
    #
    # https://github.com/googleapis/google-api-python-client/issues/299#issuecomment-255793971
    #
    logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)

    configure_script_logging(log)
    mode_parser = argparse.ArgumentParser(add_help=False)
    mode_group = mode_parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--create', '-c', action='store_true', dest='create',
        help='Idempotently create credentials instead of destroying them.'
    )
    mode_group.add_argument(
        '--destroy', '-d', action='store_false', dest='create',
        help='Idempotently destroy credentials instead of creating them.'
    )

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(help='Specify action', dest='resource')
    subparsers.required = True

    sa_parser = subparsers.add_parser(
        'service_account',
        parents=[mode_parser],
        help='Create credentials for a Google service account and them in an '
             'AWS Secrets Manager secret.'
    )
    sa_parser.set_defaults(func=CredentialsProvisioner.provision_sa)
    sa_parser.add_argument(
        'email', type=str,
        help='The email address of the service account'
    )
    sa_parser.add_argument(
        'secret_name', type=str,
        help='The name of secret to store the service account credentials in'
    )

    hmac_parser = subparsers.add_parser(
        'hmac', parents=[mode_parser],
        help='Generate a random HMAC signing key and store it in an AWS '
             'Secrets Manager secret.'
    )
    hmac_parser.set_defaults(func=CredentialsProvisioner.provision_hmac)

    args = parser.parse_args()
    credentials_provisioner = CredentialsProvisioner()
    args.func(credentials_provisioner, args)
