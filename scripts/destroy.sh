#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_directory="${repository_root}/terraform"

for command in aws terraform curl; do
  command -v "${command}" >/dev/null || { echo "Missing required command: ${command}" >&2; exit 1; }
done

aws sts get-caller-identity >/dev/null || { echo "Run 'aws login' before destroying resources." >&2; exit 1; }
eval "$(aws configure export-credentials --format env)"
public_ip="${PUBLIC_IP:-$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')}"
[[ "${public_ip}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "Invalid PUBLIC_IP: ${public_ip}" >&2; exit 1; }
export TF_VAR_cluster_endpoint_public_access_cidrs="[\"${public_ip}/32\"]"

echo "WARNING: Terraform will remove the project infrastructure. Back up required S3 and EBS data before approving." >&2
terraform -chdir="${terraform_directory}" init -lockfile=readonly
terraform -chdir="${terraform_directory}" destroy "$@"
