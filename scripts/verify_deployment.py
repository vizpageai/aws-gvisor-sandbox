from __future__ import annotations

import argparse
import os
import uuid

from gvisor_sandbox import GPU, EBSBlockStorage, S3ObjectStorage, SandboxPlatform, SandboxSpec


def verify_control_plane(platform: SandboxPlatform, *, gpu_enabled: bool) -> None:
    apps = platform._apps_api()
    autoscalers = apps.list_namespaced_deployment(
        "kube-system",
        label_selector="app.kubernetes.io/instance=cluster-autoscaler",
    ).items
    if len(autoscalers) != 1 or not autoscalers[0].status.available_replicas:
        raise RuntimeError("Cluster Autoscaler deployment is not available")
    if gpu_enabled:
        apps.read_namespaced_deployment("gpu-operator", "gpu-operator")


def verify_cpu_storage(platform: SandboxPlatform, *, bucket: str, region: str, suffix: str) -> None:
    name = f"release-cpu-{suffix}"
    prefix = f"release-verification/{name}"
    sandbox = platform.create(
        name,
        SandboxSpec(
            image="python:3.12-slim",
            s3=S3ObjectStorage(bucket=bucket, prefix=prefix, region=region),
            ebs=EBSBlockStorage(size_gib=1),
        ),
        timeout_seconds=900,
    )
    try:
        result = sandbox.run_python(
            """
import pathlib
import os
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "boto3"], check=True)
import boto3

workspace = pathlib.Path("/workspace")
(workspace / "verified.txt").write_text("gvisor-ebs-ok")
s3 = boto3.client("s3")
s3.put_object(Bucket=os.environ["SANDBOX_S3_BUCKET"], Key=os.environ["SANDBOX_S3_PREFIX"] + "/verified.txt", Body=b"irsa-ok")
assert s3.get_object(Bucket=os.environ["SANDBOX_S3_BUCKET"], Key=os.environ["SANDBOX_S3_PREFIX"] + "/verified.txt")["Body"].read() == b"irsa-ok"
s3.delete_object(Bucket=os.environ["SANDBOX_S3_BUCKET"], Key=os.environ["SANDBOX_S3_PREFIX"] + "/verified.txt")
print((workspace / "verified.txt").read_text())
""",
            timeout_seconds=900,
        )
        if not result.ok or "gvisor-ebs-ok" not in result.output:
            raise RuntimeError(f"CPU/storage verification failed: {result}")
        status = sandbox.status()
        if status.runtime_class_name != "gvisor":
            raise RuntimeError(f"CPU sandbox used unexpected runtime {status.runtime_class_name!r}")
    finally:
        sandbox.delete(delete_storage=True)


def verify_gpu(platform: SandboxPlatform, *, suffix: str) -> None:
    name = f"release-gpu-{suffix}"
    spec = SandboxSpec(
        image="pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime",
        gpu=GPU.from_type("nvidia-gpu"),
    )
    try:
        result = platform.submit_python(
            """
import torch
assert torch.cuda.is_available(), "CUDA is unavailable"
print(torch.cuda.get_device_name(0))
print((torch.ones(16, device="cuda") * 2).sum().item())
""",
            name=name,
            spec=spec,
            timeout_seconds=2400,
            ttl_seconds_after_finished=3600,
        )
        if not result.ok or result.runtime_class_name != "gvisor-nvproxy":
            raise RuntimeError(f"GPU verification failed: {result}")
    finally:
        try:
            platform.job(name).delete()
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run destructive, self-cleaning acceptance tests against a deployed cluster.")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument("--skip-gpu", action="store_true")
    args = parser.parse_args()

    suffix = uuid.uuid4().hex[:8]
    platform = SandboxPlatform(namespace=args.namespace)
    verify_control_plane(platform, gpu_enabled=not args.skip_gpu)
    verify_cpu_storage(platform, bucket=args.s3_bucket, region=args.region, suffix=suffix)
    if not args.skip_gpu:
        verify_gpu(platform, suffix=suffix)
    print("Deployment verification passed.")


if __name__ == "__main__":
    main()
