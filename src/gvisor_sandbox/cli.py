from __future__ import annotations

import argparse
import pathlib
import sys

from .client import GvisorSandbox
from .platform import JobHandle, SandboxPlatform
from .types import (
    GPU,
    ComputeResources,
    EBSBlockStorage,
    RuntimeMode,
    S3ObjectStorage,
    SandboxSpec,
    SchedulingError,
    UnsupportedConfiguration,
)

COMMANDS = {
    "create",
    "list",
    "status",
    "start",
    "stop",
    "delete",
    "exec",
    "python",
    "job-run",
    "job-list",
    "job-status",
    "job-logs",
    "job-wait",
    "job-delete",
    "gc",
}


def _key_values(values: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise ValueError(f"Expected KEY=VALUE, got {value!r}")
        result[key] = item
    return result


def _add_cluster_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--context", help="kubectl context; defaults to the active context")
    parser.add_argument(
        "--service-account",
        help="Kubernetes service account; S3 workloads default to gvisor-sandbox, others use the namespace default",
    )
    parser.add_argument("--cpu-node-selector", action="append", metavar="KEY=VALUE")
    parser.add_argument("--gpu-node-selector", action="append", metavar="KEY=VALUE")


def _add_spec_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--runtime", choices=[item.value for item in RuntimeMode], default="auto")
    parser.add_argument("--gpu-type", help="For example nvidia-l4, nvidia-tesla-t4, or nvidia.com/gpu")
    parser.add_argument("--gpu-count", type=int, default=1)
    parser.add_argument("--node-selector", action="append", metavar="KEY=VALUE")
    parser.add_argument("--cpu", default="500m")
    parser.add_argument("--memory", default="1Gi")
    parser.add_argument("--cpu-limit")
    parser.add_argument("--memory-limit")
    parser.add_argument("--ephemeral-storage")
    parser.add_argument("--env", action="append", metavar="KEY=VALUE")
    parser.add_argument("--label", action="append", metavar="KEY=VALUE")
    parser.add_argument("--s3-uri")
    parser.add_argument("--aws-region")
    parser.add_argument("--ebs-size", type=int, metavar="GIB")
    parser.add_argument("--storage-class", default="gp3")
    parser.add_argument("--mount-path", default="/workspace")
    parser.add_argument("--pvc-name")
    parser.add_argument("--working-dir")
    parser.add_argument("--ttl", type=int, metavar="SECONDS", help="Wall-clock lifetime used by the gc command")


def _platform(args: argparse.Namespace) -> SandboxPlatform:
    return SandboxPlatform(
        namespace=args.namespace,
        kube_context=args.context,
        cpu_node_selector=_key_values(args.cpu_node_selector) if args.cpu_node_selector else None,
        gpu_node_selector=_key_values(args.gpu_node_selector),
        service_account_name=args.service_account,
    )


def _spec(args: argparse.Namespace) -> SandboxSpec:
    gpu = GPU.from_type(args.gpu_type, count=args.gpu_count) if args.gpu_type else None
    s3 = S3ObjectStorage.from_uri(args.s3_uri, region=args.aws_region) if args.s3_uri else None
    ebs = None
    if args.ebs_size:
        ebs = EBSBlockStorage(
            size_gib=args.ebs_size,
            storage_class_name=args.storage_class,
            mount_path=args.mount_path,
            pvc_name=args.pvc_name,
        )
    return SandboxSpec(
        image=args.image,
        runtime=args.runtime,
        gpu=gpu,
        resources=ComputeResources(
            cpu=args.cpu,
            memory=args.memory,
            ephemeral_storage=args.ephemeral_storage,
            cpu_limit=args.cpu_limit,
            memory_limit=args.memory_limit,
        ),
        node_selector=_key_values(args.node_selector),
        service_account_name=args.service_account,
        env=_key_values(args.env),
        labels=_key_values(args.label),
        s3=s3,
        ebs=ebs,
        working_dir=args.working_dir,
        ttl_seconds=args.ttl,
    )


def _read_source(path: str) -> str:
    return sys.stdin.read() if path == "-" else pathlib.Path(path).read_text(encoding="utf-8")


def _print_status(status) -> None:
    gpu = "none" if status.gpu is None else f"{status.gpu.type}x{status.gpu.count}"
    print(
        f"name={status.name} phase={status.phase} ready={str(status.ready).lower()} "
        f"runtime={status.runtime_class_name or 'native'} gpu={gpu} node={status.node_name or '-'}"
    )


def _legacy_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gvisor-sandbox")
    parser.add_argument("source", help="Python file to run, or '-' to read code from stdin.")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--runtime-class", default="gvisor")
    parser.add_argument("--image", default="python:3.12-alpine")
    parser.add_argument("--name")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--gpu-type")
    parser.add_argument("--gpu-count", type=int, default=1)
    parser.add_argument(
        "--gpu-runtime-class",
        default="gvisor-nvproxy",
        help="GPU RuntimeClass; use 'native' only for trusted compatibility workloads",
    )
    parser.add_argument("--fail-fast-unschedulable", action="store_true")
    args = parser.parse_args(argv)
    gpu = GPU.from_type(args.gpu_type, count=args.gpu_count) if args.gpu_type else None
    gpu_runtime_class = None if args.gpu_runtime_class == "native" else args.gpu_runtime_class
    sandbox = GvisorSandbox(
        namespace=args.namespace,
        runtime_class=args.runtime_class,
        cleanup=not args.no_cleanup,
        gpu_runtime_class=gpu_runtime_class,
    )
    result = sandbox.run_python(
        _read_source(args.source),
        image=args.image,
        name=args.name,
        timeout_seconds=args.timeout,
        gpu=gpu,
        fail_fast_unschedulable=args.fail_fast_unschedulable,
    )
    print(result.logs, end="" if result.logs.endswith("\n") else "\n")
    print(f"pod={result.name} phase={result.phase} exit_code={result.exit_code} node={result.node_name}", file=sys.stderr)
    return 0 if result.ok else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gvisor-sandbox",
        description="Create CPU/GPU agent sandboxes and run Kubernetes jobs on AWS EKS.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a reusable sandbox")
    create.add_argument("name")
    _add_cluster_options(create)
    _add_spec_options(create)
    create.add_argument("--stopped", action="store_true")
    create.add_argument("--no-wait", action="store_true")
    create.add_argument("--timeout", type=int, default=600)

    listing = subparsers.add_parser("list", help="List reusable sandboxes")
    _add_cluster_options(listing)

    for command in ("status", "start", "stop"):
        item = subparsers.add_parser(command)
        item.add_argument("name")
        _add_cluster_options(item)
        item.add_argument("--timeout", type=int, default=600)
        item.add_argument("--no-wait", action="store_true")

    delete = subparsers.add_parser("delete")
    delete.add_argument("name")
    _add_cluster_options(delete)
    delete.add_argument("--storage", action="store_true", help="Also delete the EBS PVC")

    execute = subparsers.add_parser("exec", help="Run a command in a reusable sandbox")
    execute.add_argument("name")
    _add_cluster_options(execute)
    execute.add_argument("--timeout", type=int, default=300)
    execute.add_argument("exec_command", nargs=argparse.REMAINDER)

    python_command = subparsers.add_parser("python", help="Run Python in a reusable sandbox")
    python_command.add_argument("name")
    python_command.add_argument("source")
    _add_cluster_options(python_command)
    python_command.add_argument("--timeout", type=int, default=300)

    job_run = subparsers.add_parser("job-run", help="Submit a short or long-running Python job")
    job_run.add_argument("source")
    job_run.add_argument("--name")
    _add_cluster_options(job_run)
    _add_spec_options(job_run)
    job_run.add_argument("--detach", action="store_true")
    job_run.add_argument("--timeout", type=int, default=3600)
    job_run.add_argument("--ttl-after-finished", type=int)

    job_list = subparsers.add_parser("job-list")
    _add_cluster_options(job_list)

    for command in ("job-status", "job-logs", "job-wait", "job-delete"):
        item = subparsers.add_parser(command)
        item.add_argument("name")
        _add_cluster_options(item)
        if command == "job-wait":
            item.add_argument("--timeout", type=int, default=3600)
        if command == "job-delete":
            item.add_argument("--storage", action="store_true")

    gc = subparsers.add_parser("gc", help="Delete resources whose --ttl has expired")
    _add_cluster_options(gc)
    gc.add_argument("--storage", action="store_true")
    return parser


def _platform_main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    platform = _platform(args)

    if args.command == "create":
        handle = platform.create(
            args.name,
            _spec(args),
            start=not args.stopped,
            wait=not args.no_wait,
            timeout_seconds=args.timeout,
        )
        _print_status(handle.status())
    elif args.command == "list":
        for status in platform.list():
            _print_status(status)
    elif args.command == "status":
        _print_status(platform.status(args.name))
    elif args.command == "start":
        _print_status(platform.start(args.name, wait=not args.no_wait, timeout_seconds=args.timeout))
    elif args.command == "stop":
        _print_status(platform.stop(args.name, wait=not args.no_wait, timeout_seconds=args.timeout))
    elif args.command == "delete":
        platform.delete(args.name, delete_storage=args.storage)
        print(f"deleted sandbox {args.name} storage={str(args.storage).lower()}")
    elif args.command == "exec":
        command = args.exec_command[1:] if args.exec_command[:1] == ["--"] else args.exec_command
        if not command:
            raise ValueError("exec requires a command after the sandbox name")
        command_result = platform.sandbox(args.name).run(command, timeout_seconds=args.timeout)
        if command_result.output:
            print(command_result.output)
        return command_result.exit_code
    elif args.command == "python":
        command_result = platform.sandbox(args.name).run_python(_read_source(args.source), timeout_seconds=args.timeout)
        if command_result.output:
            print(command_result.output)
        return command_result.exit_code
    elif args.command == "job-run":
        submitted = platform.submit_python(
            _read_source(args.source),
            name=args.name,
            spec=_spec(args),
            wait=not args.detach,
            timeout_seconds=args.timeout,
            ttl_seconds_after_finished=args.ttl_after_finished,
        )
        if isinstance(submitted, JobHandle):
            print(f"job={submitted.name} status={submitted.status()}")
        else:
            print(submitted.logs, end="" if submitted.logs.endswith("\n") else "\n")
            print(f"job={submitted.name} phase={submitted.phase} exit_code={submitted.exit_code}", file=sys.stderr)
            return 0 if submitted.ok else 1
    elif args.command == "job-list":
        for name in platform.list_jobs():
            print(f"name={name} status={platform.job(name).status()}")
    elif args.command == "job-status":
        print(f"name={args.name} status={platform.job(args.name).status()}")
    elif args.command == "job-logs":
        print(platform.job(args.name).logs())
    elif args.command == "job-wait":
        job_result = platform.job(args.name).wait(timeout_seconds=args.timeout)
        print(job_result.logs, end="" if job_result.logs.endswith("\n") else "\n")
        return 0 if job_result.ok else 1
    elif args.command == "job-delete":
        platform.job(args.name).delete(delete_storage=args.storage)
        print(f"deleted job {args.name} storage={str(args.storage).lower()}")
    elif args.command == "gc":
        for name in platform.cleanup_expired(delete_storage=args.storage):
            print(f"deleted expired resource {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] not in COMMANDS and argv[0] not in {"-h", "--help"}:
            return _legacy_main(argv)
        return _platform_main(argv)
    except UnsupportedConfiguration as exc:
        print(f"unsupported configuration: {exc}", file=sys.stderr)
        return 2
    except SchedulingError as exc:
        print(f"scheduling failed: {exc}", file=sys.stderr)
        return 3
    except (ValueError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
