# Operations runbook

## Acceptance

Run `scripts/deploy.ps1` or `scripts/deploy.sh` for a new installation. Rerun
`scripts/verify_deployment.py` after infrastructure, Kubernetes, runtime, or
GPU-driver changes. It creates uniquely named resources and removes them after
the test. GPU verification can take tens of minutes when scaling from zero.

## Routine checks

- Confirm EKS control-plane and VPC flow logs are arriving in CloudWatch.
- Check `cluster-autoscaler` logs for failed scale-ups and exhausted quotas.
- Check GPU Operator ClusterPolicy status and validator pods.
- Monitor EBS, S3, NAT Gateway, EKS, and GPU EC2 cost.
- Run `gvisor-sandbox gc` on a schedule if wall-clock sandbox TTLs are used.
- Review Dependabot updates and rerun acceptance tests before merging them.

## Data protection

S3 versioning is enabled and EBS volumes are encrypted. PVC deletion deletes
the associated EBS volume, and `delete_storage=True` is intentionally
destructive. Configure an organization-approved AWS Backup plan for production
workspace volumes before storing irreplaceable data. Test restores regularly.

## Upgrades

Cluster Autoscaler must match the Kubernetes control-plane minor version.
Upgrade EKS one minor version at a time and replace/update worker AMIs at every
step. Upgrades drain and replace workers, so review PodDisruptionBudgets,
backups, available capacity, and maintenance windows first. The
`scripts/upgrade-eks-to-1.36.ps1` helper reconciles the current minor before
advancing through each supported minor from 1.32 to 1.36. Review every
interactive Terraform plan; do not use it as an unattended migration tool.

## Incident response

1. Stop affected sandboxes or scale their Deployments to zero.
2. Preserve CloudWatch control-plane logs, VPC flow logs, Kubernetes events,
   pod specifications, and relevant S3 object versions.
3. Rotate exposed IAM roles and credentials.
4. Replace affected nodes; do not rely on in-place cleanup after a suspected
   node compromise.
5. Report project vulnerabilities according to `SECURITY.md`.

GPU jobs use the native host kernel. Run mutually untrusted GPU tenants in
separate AWS accounts and clusters.
