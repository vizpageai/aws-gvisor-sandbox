# gVisor on AWS EKS

Terraform project for deploying an Amazon EKS cluster with a dedicated gVisor node group. The node group uses the EKS optimized Amazon Linux 2 AMI, installs `runsc`, registers the `runsc` containerd runtime handler, and exposes it through a Kubernetes `RuntimeClass` named `gvisor`.

## What This Creates

- VPC with public and private subnets across three Availability Zones.
- EKS control plane.
- Managed node group for gVisor workloads.
- `node.k8s.io/v1` `RuntimeClass` with `handler: runsc`.
- Optional smoke-test pod that runs with `runtimeClassName: gvisor`.
- Optional AWS sandbox storage plane:
  - S3 bucket for object-backed workspace state.
  - IAM role and Kubernetes service account for sandbox S3 access.
  - AWS EBS CSI driver and `gp3` StorageClass for per-sandbox block storage.

## Requirements

- Terraform 1.6 or newer.
- AWS credentials with permissions to create VPC, IAM, EC2, and EKS resources.
- `kubectl` and AWS CLI for post-deploy inspection.

The default Kubernetes version is `1.32` because this project intentionally uses the EKS optimized Amazon Linux 2 AMI family for its `/etc/eks/bootstrap.sh` workflow. If you move to Kubernetes `1.33` or newer, review AWS AMI availability and migrate the bootstrap template to AL2023 `nodeadm`.

## Deploy

```bash
cd terraform
terraform init
terraform apply
```

After Terraform finishes, configure `kubectl`:

```bash
aws eks update-kubeconfig \
  --region "$(terraform -chdir=terraform output -raw region)" \
  --name "$(terraform -chdir=terraform output -raw cluster_name)"
```

Verify the runtime class and test pod:

```bash
kubectl get runtimeclass
kubectl get pod gvisor-smoke -o wide
kubectl logs gvisor-smoke
```

The smoke pod runs `dmesg`; a successful gVisor run includes startup lines from gVisor.

## Configuration

Create `terraform/terraform.tfvars` to override defaults:

```hcl
name           = "gvisor-eks"
region         = "us-east-1"
cluster_version = "1.32"

gvisor_node_instance_types = ["m6i.large"]
gvisor_desired_size        = 2
gvisor_min_size            = 1
gvisor_max_size            = 4
enable_smoke_test          = true
```

## Security Notes

gVisor adds a user-space kernel boundary for selected pods, but it is not a complete replacement for normal Kubernetes and AWS controls. Keep IAM least-privileged, use private subnets for nodes, apply network policies where appropriate, and only assign `runtimeClassName: gvisor` to workloads that have been compatibility-tested.

The reusable AWS sandbox API is an EKS/gVisor approximation of managed agent sandboxes. It preserves object and disk state through S3 and EBS-backed PVCs, but it does not preserve process memory or provide VM snapshot resume. Stop/resume deletes and recreates the pod while retaining externalized state.

## Destroy

```bash
terraform -chdir=terraform destroy
```

## Python Package

This repository also includes an installable Python package for launching code directly into Kubernetes pods that use the deployed gVisor RuntimeClass.

Install locally:

```bash
pip install -e .
```

Run code from Python:

```python
from gvisor_sandbox import GvisorSandbox

sandbox = GvisorSandbox()
result = sandbox.run_python("""
import platform

print("hello from gVisor")
print("kernel:", platform.release())
""")

print(result.logs)
print(result.node_name)
```

Run code from the CLI:

```bash
gvisor-sandbox examples/run_python.py --no-cleanup
```

The default package configuration creates pods with:

```yaml
runtimeClassName: gvisor
nodeSelector:
  runtime.gvisor.dev/enabled: "true"
```

Run a CPU machine-learning workload in the sandbox:

```bash
python examples/ml_workload.py
```

The demo installs NumPy inside the sandbox pod, trains a small logistic
regression model on synthetic data, runs batch inference, and prints training
loss, accuracy, and the gVisor node that executed the workload. It does not
require GPU nodes.

### Reusable AWS Sandbox Sessions

For agent-style workflows that need a named environment, persistent workspace disk, and shared object storage, use `AwsSandbox`:

```python
from gvisor_sandbox import AwsSandbox, EBSBlockStorage, S3ObjectStorage

sandbox = AwsSandbox(
    name="agent-dev-sandbox",
    image="python:3.12-slim",
    s3=S3ObjectStorage.from_uri("s3://my-sandbox-bucket/agent-dev-sandbox", region="us-east-1"),
    ebs=EBSBlockStorage(size_gib=8, storage_class_name="gp3", mount_path="/workspace"),
)

sandbox.start(timeout_seconds=600)

result = sandbox.run_python("""
from pathlib import Path
import os

workspace = Path(os.environ["SANDBOX_WORKSPACE"])
workspace.mkdir(parents=True, exist_ok=True)
(workspace / "hello.txt").write_text("state persisted on EBS")

print("workspace:", workspace)
print("s3 uri:", os.environ["SANDBOX_S3_URI"])
""")

print(result.logs)

sandbox.stop()   # stops compute; keeps S3 objects and the EBS PVC
sandbox.start()  # resumes with the same PVC mounted again
print(sandbox.exec(["cat", "/workspace/hello.txt"]))
```

Run the included example:

```powershell
$env:PYTHONPATH="src"
$env:SANDBOX_S3_BUCKET="$(terraform -chdir=terraform output -raw sandbox_s3_bucket_name)"
python examples\aws_sandbox_session.py
```

Delete the sandbox pod and PVC after the example:

```powershell
$env:SANDBOX_DELETE="1"
python examples\aws_sandbox_session.py
```

Mapping from the Azure sandbox concepts to this AWS implementation:

| Managed sandbox concept | AWS implementation in this repo |
| --- | --- |
| Secure code sandbox | EKS pod with `runtimeClassName: gvisor` / `runsc` |
| Blob/object workspace | S3 bucket and IRSA-backed service account permissions |
| Block storage | EBS CSI-provisioned `gp3` PersistentVolumeClaim |
| Stop/resume state | Delete/recreate pod while retaining S3 objects and PVC data |
| GPU jobs | Normal runtime GPU pods, not gVisor GPU passthrough |

### GPU Requests

The package supports declaring GPU resources through Kubernetes extended resources:

```python
from gvisor_sandbox import GPU, GvisorSandbox

sandbox = GvisorSandbox(
    runtime_class=None,
    node_selector={"accelerator": "nvidia-l4"},
)

result = sandbox.run_python(
    "print('gpu workload placeholder')",
    image="python:3.12",
    gpu=GPU.from_type("nvidia-l4", count=1),
    timeout_seconds=900,
)
```

By default, GPU requests are rejected when `runtime_class="gvisor"` because GPU device passthrough is not something this EKS gVisor deployment provides. To run real GPU workloads, add GPU-capable nodes and a device plugin such as the NVIDIA Kubernetes device plugin, then target a GPU-capable runtime or node pool explicitly.

`run_python()` waits through Kubernetes `FailedScheduling` events until `timeout_seconds` expires. This allows clusters with cluster-autoscaler or Karpenter to scale GPU nodes from zero. If you want the older fail-fast behavior, pass `fail_fast_unschedulable=True`.

If your cluster does not run an autoscaler, scale the GPU node group before submitting the job, for example:

```powershell
aws eks update-nodegroup-config `
  --region us-east-1 `
  --cluster-name gvisor-eks `
  --nodegroup-name gvisor-eks-gpu-test `
  --scaling-config minSize=0,maxSize=1,desiredSize=1
```
