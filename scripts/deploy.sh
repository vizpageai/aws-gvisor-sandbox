#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_directory="${repository_root}/terraform"

for command in aws terraform kubectl python curl; do
  command -v "${command}" >/dev/null || { echo "Missing required command: ${command}" >&2; exit 1; }
done

aws sts get-caller-identity >/dev/null || { echo "Run 'aws login' before deploying." >&2; exit 1; }
# Make AWS CLI login-session credentials available to Terraform's AWS SDK for
# this process only. The temporary values are neither printed nor persisted.
eval "$(aws configure export-credentials --format env)"
public_ip="${PUBLIC_IP:-$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')}"
[[ "${public_ip}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "Invalid PUBLIC_IP: ${public_ip}" >&2; exit 1; }

terraform -chdir="${terraform_directory}" init
terraform -chdir="${terraform_directory}" apply \
  -var="cluster_endpoint_public_access_cidrs=[\"${public_ip}/32\"]" "$@"

region="$(terraform -chdir="${terraform_directory}" output -raw region)"
cluster_name="$(terraform -chdir="${terraform_directory}" output -raw cluster_name)"
bucket="$(terraform -chdir="${terraform_directory}" output -raw sandbox_s3_bucket_name)"
aws eks update-kubeconfig --region "${region}" --name "${cluster_name}"
python -m pip install "${repository_root}"

verify_args=("${repository_root}/scripts/verify_deployment.py" --region "${region}" --s3-bucket "${bucket}")
if [[ "${SKIP_GPU_VERIFICATION:-0}" == "1" ]]; then verify_args+=(--skip-gpu); fi
python "${verify_args[@]}"

echo "Sandbox platform is ready. Run: gvisor-sandbox create my-agent --ebs-size 8"
