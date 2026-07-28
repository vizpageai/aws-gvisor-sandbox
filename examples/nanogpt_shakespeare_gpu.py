from __future__ import annotations

import argparse
import csv
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from gvisor_sandbox import GPU, GvisorSandbox


EVAL_RE = re.compile(r"step\s+(\d+):\s+train loss\s+([0-9.]+),\s+val loss\s+([0-9.]+)")
ITER_RE = re.compile(r"iter\s+(\d+):\s+loss\s+([0-9.]+),")


@dataclass(frozen=True)
class LossPoint:
    iteration: int
    train_loss: float | None = None
    val_loss: float | None = None
    iter_loss: float | None = None


def run_local(command: list[str], *, check: bool = True) -> str:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{details}")
    return completed.stdout.strip()


def scale_nodegroup(region: str, cluster: str, nodegroup: str, desired: int) -> None:
    run_local(
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
        output = run_local(
            [
                "kubectl",
                "get",
                "nodes",
                "-l",
                selector,
                "-o",
                r"jsonpath={range .items[*]}{.metadata.name}{' gpu='}{.status.allocatable.nvidia\.com/gpu}{'\n'}{end}",
            ],
            check=False,
        )
        print(output or "waiting for GPU node...")
        if "gpu=1" in output:
            return
        time.sleep(15)

    raise TimeoutError(f"Timed out waiting for node selector {selector} with nvidia.com/gpu=1")


def parse_losses(logs: str) -> list[LossPoint]:
    points: list[LossPoint] = []
    for line in logs.splitlines():
        if match := EVAL_RE.search(line):
            points.append(
                LossPoint(
                    iteration=int(match.group(1)),
                    train_loss=float(match.group(2)),
                    val_loss=float(match.group(3)),
                )
            )
            continue
        if match := ITER_RE.search(line):
            points.append(LossPoint(iteration=int(match.group(1)), iter_loss=float(match.group(2))))
    return points


def write_csv(path: Path, points: list[LossPoint]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["iteration", "train_loss", "val_loss", "iter_loss"])
        writer.writeheader()
        for point in points:
            writer.writerow(
                {
                    "iteration": point.iteration,
                    "train_loss": point.train_loss if point.train_loss is not None else "",
                    "val_loss": point.val_loss if point.val_loss is not None else "",
                    "iter_loss": point.iter_loss if point.iter_loss is not None else "",
                }
            )


def write_svg(path: Path, points: list[LossPoint]) -> None:
    series = {
        "train": [(p.iteration, p.train_loss) for p in points if p.train_loss is not None],
        "val": [(p.iteration, p.val_loss) for p in points if p.val_loss is not None],
        "iter": [(p.iteration, p.iter_loss) for p in points if p.iter_loss is not None],
    }
    all_points = [(x, y) for values in series.values() for x, y in values]
    if not all_points:
        path.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='900' height='520'></svg>\n", encoding="utf-8")
        return

    width, height = 900, 520
    left, right, top, bottom = 70, 30, 40, 70
    plot_w, plot_h = width - left - right, height - top - bottom
    min_x, max_x = min(x for x, _ in all_points), max(x for x, _ in all_points)
    min_y, max_y = min(y for _, y in all_points), max(y for _, y in all_points)
    if min_x == max_x:
        max_x += 1
    if min_y == max_y:
        max_y += 1
    y_pad = (max_y - min_y) * 0.08
    min_y -= y_pad
    max_y += y_pad

    def sx(x: int) -> float:
        return left + ((x - min_x) / (max_x - min_x)) * plot_w

    def sy(y: float) -> float:
        return top + (1 - ((y - min_y) / (max_y - min_y))) * plot_h

    colors = {"train": "#2563eb", "val": "#dc2626", "iter": "#16a34a"}
    labels = {"train": "eval train loss", "val": "eval val loss", "iter": "iteration loss"}
    lines: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
        "<rect width='100%' height='100%' fill='white'/>",
        f"<text x='{width / 2}' y='24' text-anchor='middle' font-family='sans-serif' font-size='18'>nanoGPT Shakespeare training curve</text>",
        f"<line x1='{left}' y1='{top}' x2='{left}' y2='{height - bottom}' stroke='#111'/>",
        f"<line x1='{left}' y1='{height - bottom}' x2='{width - right}' y2='{height - bottom}' stroke='#111'/>",
        f"<text x='{width / 2}' y='{height - 22}' text-anchor='middle' font-family='sans-serif' font-size='13'>iteration</text>",
        f"<text x='18' y='{height / 2}' transform='rotate(-90 18 {height / 2})' text-anchor='middle' font-family='sans-serif' font-size='13'>loss</text>",
    ]

    for tick in range(6):
        x_value = min_x + (max_x - min_x) * tick / 5
        x_pos = sx(int(x_value))
        lines.append(f"<line x1='{x_pos:.1f}' y1='{height - bottom}' x2='{x_pos:.1f}' y2='{height - bottom + 5}' stroke='#111'/>")
        lines.append(f"<text x='{x_pos:.1f}' y='{height - bottom + 22}' text-anchor='middle' font-family='sans-serif' font-size='11'>{x_value:.0f}</text>")
        y_value = min_y + (max_y - min_y) * tick / 5
        y_pos = sy(y_value)
        lines.append(f"<line x1='{left - 5}' y1='{y_pos:.1f}' x2='{left}' y2='{y_pos:.1f}' stroke='#111'/>")
        lines.append(f"<text x='{left - 9}' y='{y_pos + 4:.1f}' text-anchor='end' font-family='sans-serif' font-size='11'>{y_value:.2f}</text>")

    legend_y = 50
    for index, name in enumerate(["train", "val", "iter"]):
        if not series[name]:
            continue
        x0 = width - 260
        y0 = legend_y + index * 22
        lines.append(f"<line x1='{x0}' y1='{y0}' x2='{x0 + 30}' y2='{y0}' stroke='{colors[name]}' stroke-width='3'/>")
        lines.append(f"<text x='{x0 + 38}' y='{y0 + 4}' font-family='sans-serif' font-size='12'>{labels[name]}</text>")

    for name, values in series.items():
        if len(values) < 2:
            continue
        point_string = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in values)
        lines.append(
            f"<polyline fill='none' stroke='{colors[name]}' stroke-width='2' points='{point_string}'/>"
        )

    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_sandbox_code(args: argparse.Namespace) -> str:
    return f"""
from pathlib import Path
import os
import subprocess
import sys

workdir = Path("/tmp/nanogpt-work")
repo = workdir / "nanoGPT"
workdir.mkdir(parents=True, exist_ok=True)

def run(command, cwd=None):
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)

run(["bash", "-lc", "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*"])
run(["git", "clone", "--depth", "1", "https://github.com/karpathy/nanoGPT.git", str(repo)])
run([sys.executable, "-m", "pip", "install", "--quiet", "requests", "numpy"])

run([sys.executable, "data/shakespeare_char/prepare.py"], cwd=repo)

train_command = [
    sys.executable,
    "train.py",
    "config/train_shakespeare_char.py",
    "--device=cuda",
    "--compile=False",
    "--dtype=float16",
    "--eval_iters={args.eval_iters}",
    "--eval_interval={args.eval_interval}",
    "--log_interval={args.log_interval}",
    "--block_size={args.block_size}",
    "--batch_size={args.batch_size}",
    "--n_layer={args.n_layer}",
    "--n_head={args.n_head}",
    "--n_embd={args.n_embd}",
    "--max_iters={args.max_iters}",
    "--lr_decay_iters={args.max_iters}",
    "--always_save_checkpoint=False",
]
run(train_command, cwd=repo)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Train nanoGPT Shakespeare inside a single-GPU Kubernetes sandbox.")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--cluster", default="gvisor-eks")
    parser.add_argument("--nodegroup", default="gvisor-eks-gpu-test")
    parser.add_argument("--selector", default="accelerator=nvidia")
    parser.add_argument("--image", default="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--scale-up", action="store_true", help="Scale the GPU node group to desiredSize=1 before running.")
    parser.add_argument("--scale-down", action="store_true", help="Scale the GPU node group back to desiredSize=0 after running.")
    parser.add_argument("--keep-pod", action="store_true", help="Do not delete the sandbox pod after completion/failure.")
    parser.add_argument("--out-dir", default="artifacts/nanogpt-shakespeare")
    parser.add_argument("--max-iters", type=int, default=600)
    parser.add_argument("--eval-interval", type=int, default=50)
    parser.add_argument("--eval-iters", type=int, default=20)
    parser.add_argument("--log-interval", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--block-size", type=int, default=128)
    parser.add_argument("--n-layer", type=int, default=4)
    parser.add_argument("--n-head", type=int, default=4)
    parser.add_argument("--n-embd", type=int, default=256)
    args = parser.parse_args()

    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.scale_up:
        print(f"Scaling {args.nodegroup} to desiredSize=1...")
        scale_nodegroup(args.region, args.cluster, args.nodegroup, desired=1)

    print("Waiting for a GPU node...")
    wait_for_gpu_node(args.selector, args.timeout)

    selector_key, selector_value = args.selector.split("=", 1)
    sandbox = GvisorSandbox(
        runtime_class=None,
        node_selector={selector_key: selector_value},
        cleanup=not args.keep_pod,
    )

    result = sandbox.run_python(
        build_sandbox_code(args),
        image=args.image,
        gpu=GPU.from_type("nvidia.com/gpu", count=1),
        timeout_seconds=args.timeout,
        cleanup=not args.keep_pod,
    )

    log_path = output_dir / "nanogpt_train.log"
    csv_path = output_dir / "training_curve.csv"
    svg_path = output_dir / "training_curve.svg"

    log_path.write_text(result.logs, encoding="utf-8")
    points = parse_losses(result.logs)
    write_csv(csv_path, points)
    write_svg(svg_path, points)

    print(result.logs)
    print(f"pod={result.name} phase={result.phase} exit_code={result.exit_code} node={result.node_name}")
    print(f"wrote log: {log_path.resolve()}")
    print(f"wrote csv: {csv_path.resolve()}")
    print(f"wrote curve: {svg_path.resolve()}")

    if args.scale_down:
        print(f"Scaling {args.nodegroup} back to desiredSize=0...")
        scale_nodegroup(args.region, args.cluster, args.nodegroup, desired=0)

    if not result.ok:
        raise SystemExit(result.exit_code or 1)


if __name__ == "__main__":
    main()
