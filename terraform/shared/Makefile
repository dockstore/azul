.PHONY: all
all: apply

include ../../common.mk

.PHONY: clean
clean: check_env check_branch git_clean

.PHONY: state
state: check_terraform check_aws

.PHONY: initable
initable: clean state providers.tf.json backend.tf.json

.PHONY: init
init: initable
	terraform init -reconfigure -lockfile=readonly

.PHONY: config
config: init $(patsubst %.template.py,%,$(wildcard *.tf.json.template.py))

.PHONY: validate
validate: config
	terraform validate

.PHONY: rename_resources
rename_resources: validate
	python $(project_root)/scripts/rename_resources.py

.PHONY: import_resources
import_resources: rename_resources
	python $(project_root)/scripts/import_default_vpc.py

.PHONY: plan
plan: import_resources
	terraform plan

.PHONY: apply
apply: import_resources
	terraform apply

.PHONY: apply_keep_unused
apply_keep_unused:
	azul_terraform_keep_unused=1 $(MAKE) apply
	@echo -e "\n" It is safe to ignore any warnings above about azul_terraform_keep_unused. "\n"

.PHONY: auto_apply
auto_apply: import_resources
	terraform plan -no-color -out=plan.bin
	terraform show -json plan.bin > plan.json
	terraform apply -auto-approve plan.bin

.PHONY: destroy
destroy: import_resources
	terraform destroy

.PHONY: auto_destroy
auto_destroy: import_resources
	@echo '!!! All resources will be deleted in 10s, hit Ctrl-C to cancel !!!'; sleep 10
	terraform destroy -auto-approve
