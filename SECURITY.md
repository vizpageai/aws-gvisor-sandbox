# Security policy

## Supported versions

Security fixes are provided for the latest released minor version. Deployments
should use the pinned Kubernetes, gVisor, NVIDIA GPU Operator, and Cluster
Autoscaler versions from the latest repository release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
[private security advisory form](https://github.com/vizpageai/aws-gvisor-sandbox/security/advisories/new).
Include affected versions, configuration, impact, and a minimal reproduction
when possible.

## Trust boundary

CPU workloads using `runtimeClassName: gvisor` receive the gVisor userspace
kernel boundary in addition to Kubernetes container controls. GPU workloads use
the native NVIDIA container runtime and do not receive the gVisor boundary.
Treat GPU workloads as trusted code or isolate them in a dedicated AWS account
and cluster. This project does not claim hostile multi-tenant GPU isolation.

S3 access is shared by workloads using the configured sandbox service account.
Use a separate deployment, AWS account, or dedicated IAM role and bucket for
each mutually untrusted tenant.
