import json
import os
from unittest.mock import (
    PropertyMock,
    patch,
)

import git

from azul.modules import (
    load_script,
)
from azul_test_case import (
    AzulUnitTestCase,
)


class TestCheckBranch(AzulUnitTestCase):

    def test_check_branch(self):
        script = load_script('check_branch')
        check_branch = script.check_branch

        def expect_exception(branch, deployment, message):
            with self.assertRaises(script.BranchDeploymentMismatch) as e:
                check_branch(branch, deployment)
            self.assertEqual((message,), e.exception.args)

        default = {
            'develop': ['dev', 'sandbox'],
            'prod': ['prod']
        }
        with patch.dict(os.environ, azul_shared_deployments=json.dumps(default)):
            check_branch('develop', 'dev')
            check_branch('develop', 'sandbox')

            expect_exception('feature/foo', 'prod',
                             "Branch 'feature/foo' cannot be deployed to 'prod', "
                             "only personal deployments.")
            expect_exception(None, 'prod',
                             "Detached head cannot be deployed to 'prod', "
                             "only personal deployments.")

            check_branch('prod', 'hannes')
            check_branch('develop', 'hannes')

            expect_exception('prod', 'dev',
                             "Branch 'prod' cannot be deployed to 'dev', "
                             "only one of {'prod'} or personal deployments.")

            expect_exception(None, 'dev',
                             "Detached head cannot be deployed to 'dev', "
                             "only personal deployments.")

            expect_exception('feature/foo', 'sandbox',
                             "Branch 'feature/foo' cannot be deployed to 'sandbox', "
                             "only personal deployments.")

            expect_exception(None, 'sandbox',
                             "Detached head cannot be deployed to 'sandbox', "
                             "only personal deployments.")

        # GitLab overrides the configuration to allow for the deployment of
        # feature branches to the sandbox.
        gitlab = {
            **default,
            '': ['sandbox']
        }
        with patch.dict(os.environ, azul_shared_deployments=json.dumps(gitlab)):
            check_branch('feature/foo', 'sandbox')
            check_branch(None, 'sandbox')
            expect_exception('feature/foo',
                             'prod',
                             "Branch 'feature/foo' cannot be deployed to 'prod', "
                             "only one of {'sandbox'} or personal deployments.")

    def test_target_branch(self):
        script = load_script('check_branch')

        develop, prod = 'develop', 'prod'
        feature, merge = 'issues/foo/1234-bar', '2345/merge'
        cases = [
            (
                'Local build',
                feature,
                {},
                feature
            ),
            (
                'Local build with detached head',
                None,
                {},
                None
            ),
            (
                'GitHub building develop',
                develop,
                {'GITHUB_REF_NAME': develop},
                develop
            ),
            (
                'GitHub building prod',
                prod,
                {'GITHUB_REF_NAME': prod},
                prod
            ),
            (
                'GitHub PR against develop',
                merge,
                {
                    'GITHUB_REF_NAME': merge,
                    'GITHUB_HEAD_REF': feature,
                    'GITHUB_BASE_REF': develop
                },
                develop
            ),
            (
                'GitHub PR against prod',
                merge,
                {
                    'GITHUB_REF_NAME': merge,
                    'GITHUB_HEAD_REF': feature,
                    'GITHUB_BASE_REF': prod
                },
                prod
            ),
            (
                'Sandbox build on GitLab',
                None,
                {'CI_COMMIT_REF_NAME': feature},
                feature
            ),
            (
                'Non-sandbox build on GitLab',
                None,
                {'CI_COMMIT_REF_NAME': develop},
                develop
            ),
        ]
        variables = {v for case in cases for v in case[2]}
        for sub_test, current_branch, new_env, target_branch in cases:
            with self.subTest(sub_test):
                with patch.object(git.Repo, 'head', new_callable=PropertyMock) as head:
                    head.return_value.is_detached = current_branch is None
                    head.return_value.reference.name = current_branch
                    with patch.dict(os.environ) as env:
                        for variable in variables:
                            env.pop(variable, None)
                        env.update(new_env)
                        self.assertEqual(target_branch, script.target_branch())
