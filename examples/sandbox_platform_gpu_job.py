from __future__ import annotations

import os

from gvisor_sandbox import ComputeResources, GPU, SandboxPlatform, SandboxSpec


gpu_type = os.getenv("SANDBOX_GPU_TYPE", "nvidia-l4")
platform = SandboxPlatform(namespace=os.getenv("SANDBOX_NAMESPACE", "default"))
spec = SandboxSpec(
    image=os.getenv("SANDBOX_GPU_IMAGE", "pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime"),
    gpu=GPU.from_type(gpu_type),
    node_selector={"accelerator": os.getenv("SANDBOX_GPU_NODE_LABEL", gpu_type)},
    resources=ComputeResources(cpu="2", memory="8Gi"),
)

result = platform.submit_python(
    """
import torch

assert torch.cuda.is_available(), "CUDA is not available"
device = torch.cuda.get_device_name(0)
x = torch.randn(2048, 2048, device="cuda")
y = x @ x
print(f"gpu={device} result={y.mean().item():.6f}")
""",
    name=os.getenv("SANDBOX_JOB_NAME", "agent-gpu-check"),
    spec=spec,
    wait=True,
    timeout_seconds=1800,
    ttl_seconds_after_finished=3600,
)
print(result.logs)
