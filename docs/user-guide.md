# User guide

This guide covers a clean AWS deployment and normal use of the
`gvisor-sandbox` CLI and Python package. Existing EKS clusters require a
separately reviewed migration because worker replacement can restart workloads.

## Contents

1. [Understand the security model](#understand-the-security-model)
2. [Prerequisites](#prerequisites)
3. [Deploy](#deploy)
4. [Verify the installation](#verify-the-installation)
5. [Use CPU sandboxes](#use-cpu-sandboxes)
6. [Use GPU sandboxes and jobs](#use-gpu-sandboxes-and-jobs)
7. [Use persistent storage](#use-persistent-storage)
8. [Use the Python API](#use-the-python-api)
9. [Manage lifecycle and cost](#manage-lifecycle-and-cost)
10. [Troubleshoot](#troubleshoot)
11. [Remove the platform](#remove-the-platform)

## Understand the security model

The platform provides two different execution boundaries:

| Workload | Runtime | Intended code |
| --- | --- | --- |
| CPU | gVisor `runsc` | Agent-generated or less-trusted code |
| NVIDIA GPU | gVisor `runsc --nvproxy` | Agent-generated or less-trusted code |

gVisor reduces direct interaction with the host kernel; it does not replace
IAM isolation, Kubernetes authorization, network controls, patching, or account
separation. nvproxy keeps GPU workloads inside gVisor and forwards a restricted
NVIDIA ioctl surface, but those calls still reach the host kernel driver. A
driver vulnerability can therefore cross the intended boundary. Do not place
mutually hostile GPU tenants in one cluster; use separate AWS accounts and
clusters or a reviewed VM-based GPU boundary.

The default sandbox service account shares access to the configured S3 bucket.
The project therefore targets one trusted team, not public multi-tenancy.

## Prerequisites

Install:

- AWS CLI v2 with `aws login` support or another short-lived credential flow.
- Terraform 1.6 or newer.
- kubectl.
- Python 3.10 or newer.
- Git.

Your AWS identity needs permission to manage VPC, IAM, EC2, Auto Scaling, EKS,
KMS, S3, CloudWatch Logs, and related service-linked roles. Use an IAM Identity
Center role or dedicated deployment role. Do not deploy routinely as the AWS
account root user.

Before enabling GPU verification, confirm that the target region offers
the configured GPU instance types and that the account has On-Demand G-instance quota. A failed quota
check normally leaves the GPU pod Pending while Cluster Autoscaler reports the
EC2 error.

## Deploy

Clone the repository and enter it:

```bash
git clone https://github.com/vizpageai/aws-gvisor-sandbox.git
cd aws-gvisor-sandbox
```

### Windows PowerShell

```powershell
aws login
.\scripts\deploy.ps1
```

Terraform displays the proposed changes and asks for confirmation. To approve
without the prompt:

```powershell
.\scripts\deploy.ps1 -AutoApprove
```

To supply the EKS endpoint address rather than discovering it:

```powershell
.\scripts\deploy.ps1 -PublicIp 203.0.113.10
```

To avoid launching a GPU during acceptance testing:

```powershell
.\scripts\deploy.ps1 -SkipGpuVerification
```

### Linux or macOS

```bash
aws login
bash scripts/deploy.sh
```

Arguments after the script name are passed to `terraform apply`:

```bash
bash scripts/deploy.sh -auto-approve
```

Set `PUBLIC_IP` to override address discovery or
`SKIP_GPU_VERIFICATION=1` to omit the CUDA test.

### What the deployment creates

- A VPC spanning three Availability Zones, with private worker subnets.
- An EKS 1.36 control plane with a private endpoint and restricted public
  endpoint.
- One Ubuntu 24.04 CPU node by default, with gVisor installed.
- An Ubuntu 24.04 GPU node group with gVisor nvproxy and desired/minimum size
  zero.
- Cluster Autoscaler, NVIDIA GPU Operator, EBS CSI, and VPC CNI network policy.
- An encrypted/versioned S3 bucket, IRSA role, and encrypted `gp3` storage.
- CloudWatch control-plane logs and VPC flow logs.

Copy `terraform/terraform.tfvars.example` to
`terraform/terraform.tfvars` for persistent configuration. Never commit the
resulting `.tfvars` file or Terraform state.

Useful settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `region` | `us-east-1` | AWS region |
| `cluster_version` | `1.36` | EKS and Ubuntu worker Kubernetes version |
| `high_availability_nat_gateway` | `true` | One NAT gateway per Availability Zone |
| `gvisor_min_size` | `1` | Always-available CPU/system capacity |
| `gpu_min_size` | `0` | Permits zero idle GPU nodes |
| `gpu_max_size` | `1` | Maximum automatically provisioned GPU nodes |
| `gpu_node_instance_types` | `["g5.xlarge", "g4dn.xlarge", "g6.xlarge"]` | nvproxy-supported GPU EC2 types, ordered by preference |
| `gpu_driver_version` | `590.48.01` | Driver ABI pinned to the gVisor release |
| `enable_cluster_autoscaler` | `true` | Pod-driven node scaling |
| `enable_sandbox_s3` | `true` | S3 and sandbox IRSA integration |
| `enable_ebs_csi` | `true` | Persistent EBS workspaces |

## Verify the installation

The deployment helper runs the comprehensive verification automatically. To
rerun it later:

```powershell
$region = terraform -chdir=terraform output -raw region
$bucket = terraform -chdir=terraform output -raw sandbox_s3_bucket_name
python scripts\verify_deployment.py --region $region --s3-bucket $bucket
```

The check validates gVisor, EBS, S3 through IRSA, Cluster Autoscaler, GPU
Operator, GPU scale-from-zero, and CUDA. It removes its temporary sandboxes and
jobs when complete.

Quick health checks:

```bash
kubectl get nodes -L runtime.gvisor.dev/enabled,accelerator
kubectl get runtimeclass gvisor gvisor-nvproxy
kubectl get deployment cluster-autoscaler -n kube-system
kubectl get pods -n gpu-operator
```

## Use CPU sandboxes

Create a reusable sandbox with an 8 GiB EBS workspace:

```bash
gvisor-sandbox create agent-one --ebs-size 8
```

Run commands or Python:

```bash
gvisor-sandbox exec agent-one -- python --version
gvisor-sandbox python agent-one examples/run_python.py
```

Inspect and manage it:

```bash
gvisor-sandbox status agent-one
gvisor-sandbox stop agent-one
gvisor-sandbox start agent-one
gvisor-sandbox delete agent-one
```

Stopping releases the pod's compute while preserving its Deployment and PVC.
Delete with `--storage` only when the persistent workspace may also be deleted:

```bash
gvisor-sandbox delete agent-one --storage
```

## Use GPU sandboxes and jobs

Create a reusable NVIDIA GPU sandbox:

```powershell
gvisor-sandbox create gpu-agent `
  --gpu-type nvidia-gpu `
  --gpu-count 1 `
  --image pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime `
  --cpu 2 --memory 8Gi --timeout 2400
```

If the GPU group is at zero, the pod remains Pending while Cluster Autoscaler
starts an available A10G, T4, or L4 instance from the configured preference list. Initial startup and driver validation can take several
minutes. The `--timeout` must be long enough for that process.

Verify CUDA:

```powershell
gvisor-sandbox exec gpu-agent -- `
  python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

Run Meta's Muse Glimmer 30B GGUF on an autoscaled 24 GB GPU:

```powershell
python examples/muse_glimmer_gpu_job.py
```

The example downloads Meta's 16.8 GB quantized text model, runs it with a
pinned CUDA-enabled llama.cpp image, verifies its response, and deletes the
job. Allow time for node startup, GPU Operator initialization, image pulling,
and the model download. Set `MUSE_GLIMMER_PROMPT` to test another prompt.

Submit a one-off Python job:

```powershell
gvisor-sandbox job-run train.py `
  --name training-1 `
  --gpu-type nvidia-gpu `
  --gpu-count 1 `
  --image pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime `
  --timeout 2400
```

For a detached job:

```bash
gvisor-sandbox job-run train.py --name training-1 --gpu-type nvidia-gpu --detach
gvisor-sandbox job-status training-1
gvisor-sandbox job-logs training-1
gvisor-sandbox job-wait training-1 --timeout 86400
gvisor-sandbox job-delete training-1
```

Runtime `auto` selects `gvisor` for CPU and `gvisor-nvproxy` for GPU. You may
also request `--runtime gvisor-nvproxy` explicitly. `--runtime native` bypasses
gVisor and is provided only for trusted workloads that are incompatible with
nvproxy; never use that fallback for untrusted code.

Confirm the runtime used by a sandbox:

```bash
gvisor-sandbox status gpu-agent
kubectl get pod -l sandbox.platform/name=gpu-agent -o jsonpath='{.items[0].spec.runtimeClassName}{"\n"}'
```

The value must be `gvisor-nvproxy` for an isolated GPU sandbox.

## Use persistent storage

### EBS filesystem

`--ebs-size 20` creates a `gp3` PVC mounted at `/workspace`. Use
`--mount-path` to change the path or `--pvc-name` to attach an existing claim.
The disk remains billable while the PVC exists, even when the sandbox is
stopped.

### S3 object storage

```bash
gvisor-sandbox create data-agent \
  --s3-uri s3://YOUR_BUCKET/data-agent \
  --aws-region us-east-1
```

The container receives `SANDBOX_S3_URI`, `SANDBOX_S3_BUCKET`, and
`SANDBOX_S3_PREFIX`. Use boto3 or the AWS CLI inside the sandbox. S3 is object
storage, not a POSIX filesystem mount.

## Use the Python API

```python
from gvisor_sandbox import EBSBlockStorage, SandboxPlatform, SandboxSpec

platform = SandboxPlatform(namespace="default")
sandbox = platform.create(
    "research-agent",
    SandboxSpec(
        image="python:3.12-slim",
        ebs=EBSBlockStorage(size_gib=20),
    ),
    timeout_seconds=900,
)

result = sandbox.run_python("print('hello from gVisor')")
print(result.output)
sandbox.stop()
```

GPU job example:

```python
from gvisor_sandbox import GPU, SandboxPlatform, SandboxSpec

platform = SandboxPlatform()
result = platform.submit_python(
    "import torch; print(torch.cuda.get_device_name(0))",
    name="gpu-check",
    spec=SandboxSpec(
        image="pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime",
        gpu=GPU.from_type("nvidia-gpu"),
    ),
    timeout_seconds=2400,
)
print(result.logs)
```

See `examples/` for persistent sessions, detached jobs, ML workloads, nanoGPT,
and OpenAI-compatible vLLM serving.

## Manage lifecycle and cost

A zero-sized GPU group avoids GPU EC2 charges, but the overall platform is not
free when idle. EKS, the CPU node, NAT gateways, public IPv4 addresses,
persistent storage, logs, and data transfer remain billable.

Development environments can use one NAT gateway:

```hcl
high_availability_nat_gateway = false
```

This lowers cost and availability. Production defaults use one NAT gateway per
Availability Zone.

Set `--ttl SECONDS` on sandboxes or jobs, then run garbage collection from a
scheduler:

```bash
gvisor-sandbox gc
```

Jobs also support Kubernetes-native cleanup with
`--ttl-after-finished SECONDS`.

## Troubleshoot

### AWS CLI works but Terraform reports no credentials

Use the deployment scripts. They export the short-lived `aws login` session to
Terraform only for the script process and do not print or persist it.

### EKS endpoint is unreachable

Your public IP may have changed. Rerun the deployment helper with the new IP,
or update `cluster_endpoint_public_access_cidrs` from a network that still has
cluster access.

### GPU pod remains Pending

Inspect scheduling events and autoscaler logs:

```bash
kubectl describe pod POD_NAME
kubectl logs -n kube-system deployment/cluster-autoscaler --tail=200
```

Common causes are G-instance quota, unavailable `g6` capacity, an
accelerator-label mismatch, missing autoscaler ASG tags, or GPU Operator not
being ready.

### GPU node exists but CUDA is unavailable

```bash
kubectl get pods -n gpu-operator
kubectl get clusterpolicy
kubectl describe node NODE_NAME
```

Wait for the NVIDIA driver, toolkit, validator, and device-plugin components.
If the pod starts but nvproxy reports an unsupported driver, verify the pinned
pair and replace the GPU nodes after correcting it:

```bash
terraform -chdir=terraform output gpu_runtime_class_name
kubectl logs -n gpu-operator -l app=nvidia-operator-validator --all-containers --tail=200
```

Do not change `gpu_driver_version` independently of `gvisor_release_version`.
GPU node bootstrap fails closed if the selected gVisor binary does not list the
configured driver ABI as supported.

### CPU pod fails under gVisor

Check the RuntimeClass and node label:

```bash
kubectl get runtimeclass gvisor -o yaml
kubectl get nodes -l runtime.gvisor.dev/enabled=true
kubectl describe pod POD_NAME
```

Some kernel features and privileged workloads are intentionally incompatible
with gVisor. Use a compatible image; do not bypass the isolation controls for
untrusted code.

### PVC remains Pending

Check the EBS CSI controller, StorageClass, and PVC events:

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver
kubectl get storageclass gp3
kubectl describe pvc PVC_NAME
```

## Remove the platform

Back up required S3 objects and EBS workspace data, then run:

```bash
./scripts/destroy.sh
```

On Windows:

```powershell
.\scripts\destroy.ps1
```

Terraform may refuse to delete a non-empty S3 bucket, protecting stored data.
Delete or move those objects only after confirming they are no longer needed.
Review the AWS console afterward for manually created resources that were not
managed by this Terraform state.
