<!--
This is the PR template for a promotion PR against `prod`.
-->

Connected issue: #0000


## Checklist


### Author

- [ ] Target branch is `prod`
- [ ] Name of PR branch matches `promotions/yyyy-mm-dd-prod`
- [ ] On ZenHub, PR is connected to the promotion issue it resolves
- [ ] PR description links to connected issue
- [ ] Title of connected issue matches `Promotion yyyy-mm-dd`
- [ ] PR title starts with title of connected issue followed by ` prod`
- [ ] PR title references the connected issue
- [ ] The promoted issues are part of the same sprint as the connected issue


### Author (reindex, API changes)

- [ ] This PR is labeled `reindex:prod` <sub>or the changes introduced by it will not require reindexing of `prod`</sub>
- [ ] This PR is labeled `reindex:partial` and its description documents the specific reindexing procedure for `prod` <sub>or requires a full reindex or is not labeled`reindex:prod`</sub>


### Author (upgrading deployments)

- [ ] This PR is labeled `upgrade` <sub>or does not require upgrading deployments</sub>
- [ ] This PR is labeled `deploy:shared` <sub>or does not modify `docker_images.json`, and does not require deploying the `shared` component for any other reason</sub>
- [ ] This PR is labeled `deploy:gitlab` <sub>or does not require deploying the `gitlab` component</sub>
- [ ] This PR is labeled `deploy:runner` <sub>or does not require deploying the `runner` image</sub>


### Author (before every review)

- [ ] PR branch is up to date (if not, merge `prod` into PR branch to integrate upstream changes)


### System administrator (after approval)

- [ ] Actually approved the PR
- [ ] Labeled PR as `no sandbox`
- [ ] Moved connected issue to *Approved* column
- [ ] PR is assigned to only the operator


### Operator (before pushing merge the commit)

- [ ] Pushed PR branch to GitHub
- [ ] Ran `_select prod.shared && CI_COMMIT_REF_NAME=prod make -C terraform/shared apply_keep_unused` <sub>or this PR is not labeled `deploy:shared`</sub>
- [ ] Ran `_select prod.gitlab && python scripts/create_gitlab_snapshot.py --no-restart` (see [operator manual](../blob/develop/OPERATOR.rst#backup-gitlab-volumes) for details) <sub>or this PR is not labeled `backup:gitlab`</sub>
- [ ] Ran `_select prod.gitlab && CI_COMMIT_REF_NAME=prod make -C terraform/gitlab apply` <sub>or this PR is not labeled `deploy:gitlab`</sub>
- [ ] Checked the items in the next section <sub>or this PR is labeled `deploy:gitlab`</sub>
- [ ] PR is assigned to only the system administrator <sub>or this PR is not labeled `deploy:gitlab`</sub>


### System administrator

- [ ] Background migrations for [`prod.gitlab`](https://gitlab.azul.data.humancellatlas.org/admin/background_migrations) are complete <sub>or this PR is not labeled `deploy:gitlab`</sub>
- [ ] PR is assigned to only the operator


### Operator (before pushing merge the commit)

- [ ] Ran `_select prod.gitlab && make -C terraform/gitlab/runner` <sub>or this PR is not labeled `deploy:runner`</sub>
- [ ] All status checks passed and the PR is mergeable
- [ ] The title of the merge commit starts with the title of this PR
- [ ] Added PR # reference to merge commit title
- [ ] Collected commit title tags in merge commit title <sub>but excluded any `p` tags</sub>
- [ ] Pushed merge commit to GitHub


### Operator (after pushing the merge commit)

- [ ] Pushed merge commit to GitLab `prod`
- [ ] Build passes on GitLab `prod`
- [ ] Reviewed build logs for anomalies on GitLab `prod`
- [ ] Ran `_select prod.shared && make -C terraform/shared apply` <sub>or this PR is not labeled `deploy:shared`</sub>
- [ ] Deleted PR branch from GitHub
- [ ] Moved connected issue to *Merged stable* column on ZenHub
- [ ] Moved promoted issues from *Merged lower* to *Merged stable* column on ZenHub
- [ ] Moved promoted issues from *Lower* to *Stable* column on ZenHub


### Operator (reindex)

- [ ] Deindexed all unreferenced catalogs in `prod` <sub>or this PR is neither labeled `reindex:partial` nor `reindex:prod`</sub>
- [ ] Deindexed specific sources in `prod` <sub>or this PR is neither labeled `reindex:partial` nor `reindex:prod`</sub>
- [ ] Indexed specific sources in `prod` <sub>or this PR is neither labeled `reindex:partial` nor `reindex:prod`</sub>
- [ ] Started reindex in `prod` <sub>or this PR does not require reindexing `prod`</sub>
- [ ] Checked for, triaged and possibly requeued messages in both fail queues in `prod` <sub>or this PR does not require reindexing `prod`</sub>
- [ ] Emptied fail queues in `prod` <sub>or this PR does not require reindexing `prod`</sub>
- [ ] Restarted the Data Browser pipeline for the [ucsc/hca/prod branch](https://gitlab.azul.data.humancellatlas.org/ucsc/data-browser/-/pipelines/new?ref=ucsc%2Fhca%2Fprod) on GitLab in `prod` <sub>or this PR does not require reindexing `prod`</sub>
- [ ] Restarted the Data Browser pipeline for the [ucsc/lungmap/prod branch](https://gitlab.azul.data.humancellatlas.org/ucsc/data-browser/-/pipelines/new?ref=ucsc%2Flungmap%2Fprod) on GitLab in `prod` <sub>or this PR does not require reindexing `prod`</sub>
- [ ] Restarted `deploy_browser` job in the GitLab pipeline for this PR in `prod` <sub>or this PR does not require reindexing `prod`</sub>


### Operator (mirroring)

- [ ] Started mirroring in `prod` <sub>or this PR does not require mirroring `prod`</sub>
- [ ] Checked for, triaged and possibly requeued messages in mirror fail queue in `prod` <sub>or this PR does not require mirroring `prod`</sub>
- [ ] Emptied mirror fail queue in `prod` <sub>or this PR does not require mirroring `prod`</sub>


### Operator

- [ ] PR is assigned to only the system administrator


### System administrator

- [ ] Removed unused image tags from [pycharm image on DockerHub](https://hub.docker.com/repository/docker/ucscgi/azul-pycharm/tags) <sub>or this promotion does not alter references to that image</sub>
- [ ] Removed unused image tags from [bigquery_emulator image on DockerHub](https://hub.docker.com/repository/docker/ucscgi/azul-bigquery-emulator/tags) <sub>or this promotion does not alter references to that image</sub>
- [ ] PR is assigned to no one


## Shorthand for review comments

- `L` line is too long
- `W` line wrapping is wrong
- `Q` bad quotes
- `F` other formatting problem
