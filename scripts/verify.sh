#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_directory="${repository_root}/terraform"
region="$(terraform -chdir="${terraform_directory}" output -raw region)"
bucket="$(terraform -chdir="${terraform_directory}" output -raw sandbox_s3_bucket_name)"
args=("${repository_root}/scripts/verify_deployment.py" --region "${region}" --s3-bucket "${bucket}")
if [[ "${SKIP_GPU_VERIFICATION:-0}" == "1" ]]; then args+=(--skip-gpu); fi
python "${args[@]}"
