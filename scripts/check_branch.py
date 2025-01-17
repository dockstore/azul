import os
import sys
from typing import (
    Sequence,
)

import git

from azul import (
    config,
)

"""
Ensure that the currently checked out branch matches the selected deployment
"""


def default_deployment(branch: str | None) -> str | None:
    deployments = config.shared_deployments_for_branch(branch)
    return None if deployments is None else deployments[0].name


class BranchDeploymentMismatch(Exception):

    def __init__(self,
                 branch: str | None,
                 deployment: config.Deployment,
                 allowed: Sequence[config.Deployment] | None
                 ) -> None:
        branch = 'Detached head' if branch is None else f'Branch {branch!r}'
        if allowed is None:
            allowed = ''
        else:
            allowed = f'one of {set(d.name for d in allowed)!r} or '
        super().__init__(f'{branch} cannot be deployed to {deployment.name!r}, '
                         f'only {allowed}personal deployments.')


def check_branch(branch: str | None, deployment: str) -> None:
    deployment = config.Deployment(deployment)
    if deployment.is_shared:
        deployments = config.shared_deployments_for_branch(branch)
        if deployments is None or deployment not in deployments:
            raise BranchDeploymentMismatch(branch, deployment, deployments)


def target_branch() -> str | None:
    """
    In a local clone, this method returns the name of the branch currently
    checked out or ``None``, if no branch is checked out (detached HEAD). On
    GitHub and GitLab this returns the name of either the branch currently being
    built or, if the build is for a feature branch involving a pull request, the
    base branch of that feature branch.
    """
    for variable in 'CI_COMMIT_REF_NAME', 'GITHUB_BASE_REF', 'GITHUB_HEAD_REF':
        try:
            branch = os.environ[variable]
        except KeyError:
            pass
        else:
            return branch
    repo = git.Repo(config.project_root)
    return None if repo.head.is_detached else repo.active_branch.name


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--print', '-P',
                        default=False,
                        action='store_true',
                        help='Print the deployment matching the current branch or exit '
                             'with non-zero status code if no such deployment exists.')
    args = parser.parse_args(argv)
    branch = target_branch()
    if args.print:
        deployment = default_deployment(branch)
        if deployment is None:
            sys.exit(1)
        else:
            print(deployment)
    else:
        check_branch(branch, config.deployment_stage)


if __name__ == '__main__':
    main(sys.argv[1:])
