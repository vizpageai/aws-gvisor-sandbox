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
`runtimeClassName: gvisor-nvproxy`. nvproxy keeps the workload inside gVisor and
only forwards NVIDIA driver operations that gVisor supports. The native runtime
is available only when an operator explicitly requests `runtime='native'`.

nvproxy does not eliminate NVIDIA kernel-driver risk: permitted operations are
forwarded to the host driver. The gVisor release and NVIDIA driver therefore
must remain pinned to a supported pair and be upgraded together. Treat a GPU
driver escape as a possible node compromise. Use separate AWS accounts and
clusters for mutually hostile tenants or workloads requiring a stronger
hardware/VM boundary. This project does not claim absolute sandboxing.

S3 access is shared by workloads using the configured sandbox service account.
Use a separate deployment, AWS account, or dedicated IAM role and bucket for
each mutually untrusted tenant.

Do not grant untrusted sandboxes a Kubernetes service-account token unless the
workload needs it. The SDK disables token automount by default. Images are not
scanned or trusted by this project; pin production images by digest and apply
your organization's admission and supply-chain policies.
