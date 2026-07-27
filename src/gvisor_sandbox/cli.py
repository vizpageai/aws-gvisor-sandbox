from __future__ import annotations

import argparse
import pathlib
import sys

from .client import GvisorSandbox
from .types import GPU, SchedulingError, UnsupportedConfiguration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gvisor-sandbox")
    parser.add_argument("source", help="Python file to run, or '-' to read code from stdin.")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--runtime-class", default="gvisor")
    parser.add_argument("--image", default="python:3.12-alpine")
    parser.add_argument("--name")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--gpu-type", help="GPU type/resource, for example nvidia-l4 or nvidia.com/gpu.")
    parser.add_argument("--gpu-count", type=int, default=1)
    parser.add_argument(
        "--allow-gpu-with-gvisor",
        action="store_true",
        help="Bypass the default guard that rejects GPU requests with runtimeClassName=gvisor.",
    )
    parser.add_argument(
        "--fail-fast-unschedulable",
        action="store_true",
        help="Fail immediately on Kubernetes FailedScheduling events instead of waiting until --timeout.",
    )
    args = parser.parse_args(argv)

    code = sys.stdin.read() if args.source == "-" else pathlib.Path(args.source).read_text(encoding="utf-8")
    gpu = GPU.from_type(args.gpu_type, count=args.gpu_count) if args.gpu_type else None

    sandbox = GvisorSandbox(
        namespace=args.namespace,
        runtime_class=args.runtime_class,
        cleanup=not args.no_cleanup,
        allow_gpu_with_gvisor=args.allow_gpu_with_gvisor,
    )

    try:
        result = sandbox.run_python(
            code,
            image=args.image,
            name=args.name,
            timeout_seconds=args.timeout,
            gpu=gpu,
            fail_fast_unschedulable=args.fail_fast_unschedulable,
        )
    except UnsupportedConfiguration as exc:
        print(f"unsupported configuration: {exc}", file=sys.stderr)
        return 2
    except SchedulingError as exc:
        print(f"scheduling failed: {exc}", file=sys.stderr)
        return 3

    print(result.logs, end="" if result.logs.endswith("\n") else "\n")
    print(f"pod={result.name} phase={result.phase} exit_code={result.exit_code} node={result.node_name}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
