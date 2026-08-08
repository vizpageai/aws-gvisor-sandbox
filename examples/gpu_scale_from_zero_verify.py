from __future__ import annotations

import argparse
import subprocess
import time

from gvisor_sandbox import GPU, GvisorSandbox


def run(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def scale_nodegroup(region: str, cluster: str, nodegroup: str, desired: int) -> None:
    run(
        [
            "aws",
            "eks",
            "update-nodegroup-config",
            "--region",
            region,
            "--cluster-name",
            cluster,
            "--nodegroup-name",
            nodegroup,
            "--scaling-config",
            f"minSize=0,maxSize=1,desiredSize={desired}",
        ]
    )


def wait_for_gpu_node(selector: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        output = run(
            [
                "kubectl",
                "get",
                "nodes",
                "-l",
                selector,
                "-o",
                r"jsonpath={range .items[*]}{.metadata.name}{' gpu='}{.status.allocatable.nvidia\.com/gpu}{'\n'}{end}",
            ]
        )
        print(output or "waiting for GPU node...")
        if "gpu=1" in output:
            return
        time.sleep(15)

    raise TimeoutError(f"Timed out waiting for a node matching {selector} with nvidia.com/gpu=1")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--cluster", default="gvisor-eks")
    parser.add_argument("--nodegroup", default="gvisor-eks-gpu-test")
    parser.add_argument("--selector", default="accelerator=nvidia")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--scale-down", action="store_true")
    args = parser.parse_args()

    print(f"Scaling {args.nodegroup} to desiredSize=1...")
    scale_nodegroup(args.region, args.cluster, args.nodegroup, desired=1)

    print("Waiting for Kubernetes to advertise nvidia.com/gpu...")
    wait_for_gpu_node(args.selector, args.timeout)

    selector_key, selector_value = args.selector.split("=", 1)
    sandbox = GvisorSandbox(
        runtime_class="gvisor-nvproxy",
        node_selector={selector_key: selector_value},
    )

    result = sandbox.run_python(
        """
import os
import subprocess

print("gpu type:", os.environ["GVISOR_SANDBOX_GPU_TYPE"])
subprocess.run(["nvidia-smi"], check=True)
try:
    import torch

    print("torch cuda available:", torch.cuda.is_available())
    print("torch device:", torch.cuda.get_device_name(0))
except ImportError:
    print("torch not installed; nvidia-smi verification succeeded")
print("GPU workload completed")
""",
        image="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
        gpu=GPU.from_type("nvidia.com/gpu", count=1),
        timeout_seconds=args.timeout,
        cleanup=False,
    )

    print(result.logs)
    print(f"pod={result.name} phase={result.phase} exit_code={result.exit_code} node={result.node_name}")

    if args.scale_down:
        print(f"Scaling {args.nodegroup} back to desiredSize=0...")
        scale_nodegroup(args.region, args.cluster, args.nodegroup, desired=0)


if __name__ == "__main__":
    main()
