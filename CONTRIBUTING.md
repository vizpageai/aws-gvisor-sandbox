# Contributing

Issues and pull requests are welcome. Keep changes focused and explain their
effect on users, AWS cost, compatibility, migration, and security.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Use short-lived AWS credentials and a disposable test account for live
infrastructure work. Never commit credentials, Terraform state, `.tfvars`
files, generated plans, model weights, or customer data.

## Required checks

```bash
ruff check .
mypy src
pytest --cov=gvisor_sandbox --cov-fail-under=35
python -m build
twine check dist/*
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false -lockfile=readonly
terraform -chdir=terraform validate
```

Include tests for behavioral changes and update the user guide for public API,
CLI, Terraform, cost, or operational changes. Changes involving EKS versions,
node images, gVisor, autoscaling, storage, or GPU drivers also require the AWS
acceptance test described in `docs/operations.md`.

## Pull requests

1. Create a focused branch from the default branch.
2. Add or update tests and documentation.
3. Run the required checks.
4. Complete the operational-impact fields in the pull-request template.
5. Request review; do not merge infrastructure changes without a rollback plan.

By submitting a contribution, you agree that it is licensed under the MIT License.
