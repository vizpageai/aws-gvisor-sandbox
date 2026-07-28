from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
import time
from pathlib import Path


APP_LABEL = "gpt-oss-20b-vllm"
MODEL_NAME = "openai/gpt-oss-20b"


def run(command: list[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, input=input_text, capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{details}")
    return completed


def kubectl_apply(yaml_text: str) -> None:
    run(["kubectl", "apply", "-f", "-"], input_text=yaml_text)


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
        completed = run(
            [
                "kubectl",
                "get",
                "nodes",
                "-l",
                selector,
                "-o",
                r"jsonpath={range .items[*]}{.metadata.name}{' gpu='}{.status.allocatable.nvidia\.com/gpu}{' instance='}{.metadata.labels.node\.kubernetes\.io/instance-type}{'\n'}{end}",
            ],
            check=False,
        )
        output = completed.stdout.strip()
        print(output or "waiting for GPU node...")
        if "gpu=1" in output:
            return
        time.sleep(15)
    raise TimeoutError(f"Timed out waiting for node selector {selector} with nvidia.com/gpu=1")


def wait_for_rollout(namespace: str, timeout_seconds: int) -> None:
    run(
        [
            "kubectl",
            "rollout",
            "status",
            f"deployment/{APP_LABEL}",
            "-n",
            namespace,
            f"--timeout={timeout_seconds}s",
        ]
    )


def wait_for_health(namespace: str, local_port: int, timeout_seconds: int) -> subprocess.Popen[str]:
    port_forward = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            "-n",
            namespace,
            f"svc/{APP_LABEL}",
            f"{local_port}:8000",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.monotonic() + timeout_seconds
    health_url = f"http://127.0.0.1:{local_port}/health"
    while time.monotonic() < deadline:
        completed = run(["curl", "-fsS", health_url], check=False)
        if completed.returncode == 0:
            return port_forward
        if port_forward.poll() is not None:
            output = port_forward.stdout.read() if port_forward.stdout else ""
            raise RuntimeError(f"kubectl port-forward exited early:\n{output}")
        time.sleep(3)

    port_forward.terminate()
    raise TimeoutError(f"Timed out waiting for {health_url}")


def resources_yaml(args: argparse.Namespace) -> str:
    node_selector_key, node_selector_value = args.selector.split("=", 1)
    hf_token_env = ""
    if args.hf_token_secret:
        hf_token_env = textwrap.dedent(
            f"""
            - name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: {args.hf_token_secret}
                  key: token
            """
        ).rstrip()

    return textwrap.dedent(
        f"""
        apiVersion: v1
        kind: PersistentVolumeClaim
        metadata:
          name: {APP_LABEL}-cache
          namespace: {args.namespace}
          labels:
            app: {APP_LABEL}
        spec:
          accessModes:
          - ReadWriteOnce
          storageClassName: {args.storage_class}
          resources:
            requests:
              storage: {args.cache_size}
        ---
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: {APP_LABEL}
          namespace: {args.namespace}
          labels:
            app: {APP_LABEL}
        spec:
          replicas: 1
          selector:
            matchLabels:
              app: {APP_LABEL}
          template:
            metadata:
              labels:
                app: {APP_LABEL}
            spec:
              runtimeClassName: null
              nodeSelector:
                {node_selector_key}: "{node_selector_value}"
              containers:
              - name: vllm
                image: {args.image}
                imagePullPolicy: IfNotPresent
                ports:
                - name: http
                  containerPort: 8000
                env:
                - name: HF_HOME
                  value: /models/huggingface
                - name: VLLM_ATTENTION_BACKEND
                  value: {args.attention_backend}
        {textwrap.indent(hf_token_env, "        ") if hf_token_env else ""}
                command:
                - bash
                - -lc
                args:
                - |
                  set -euxo pipefail
                  if ! command -v vllm >/dev/null 2>&1; then
                    python -m pip install --upgrade pip uv
                    uv pip install --system --pre vllm=={args.vllm_version} \\
                      --extra-index-url https://wheels.vllm.ai/gpt-oss/ \\
                      --extra-index-url https://download.pytorch.org/whl/nightly/cu128 \\
                      --index-strategy unsafe-best-match
                  fi
                  exec vllm serve {MODEL_NAME} \\
                    --host 0.0.0.0 \\
                    --port 8000 \\
                    --served-model-name {MODEL_NAME} \\
                    --max-model-len {args.max_model_len} \\
                    --gpu-memory-utilization {args.gpu_memory_utilization} \\
                    --trust-remote-code
                resources:
                  limits:
                    nvidia.com/gpu: "1"
                  requests:
                    nvidia.com/gpu: "1"
                volumeMounts:
                - name: model-cache
                  mountPath: /models
                readinessProbe:
                  httpGet:
                    path: /health
                    port: 8000
                  initialDelaySeconds: 30
                  periodSeconds: 10
                  timeoutSeconds: 3
                  failureThreshold: 60
                livenessProbe:
                  httpGet:
                    path: /health
                    port: 8000
                  initialDelaySeconds: 120
                  periodSeconds: 30
                  timeoutSeconds: 3
                  failureThreshold: 10
              volumes:
              - name: model-cache
                persistentVolumeClaim:
                  claimName: {APP_LABEL}-cache
        ---
        apiVersion: v1
        kind: Service
        metadata:
          name: {APP_LABEL}
          namespace: {args.namespace}
          labels:
            app: {APP_LABEL}
        spec:
          type: ClusterIP
          selector:
            app: {APP_LABEL}
          ports:
          - name: http
            port: 8000
            targetPort: 8000
        """
    ).strip() + "\n"


def write_client_example(path: Path, base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'''from openai import OpenAI

client = OpenAI(
    base_url="{base_url}",
    api_key="EMPTY",
)

response = client.chat.completions.create(
    model="{MODEL_NAME}",
    messages=[
        {{"role": "system", "content": "You are a concise assistant."}},
        {{"role": "user", "content": "Explain MXFP4 quantization in two sentences."}},
    ],
    temperature=0.2,
    max_tokens=200,
)

print(response.choices[0].message.content)
''',
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy gpt-oss-20b with vLLM and expose an OpenAI-compatible API.")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--cluster", default="gvisor-eks")
    parser.add_argument("--nodegroup", default="gvisor-eks-gpu-test")
    parser.add_argument("--selector", default="accelerator=nvidia")
    parser.add_argument("--storage-class", default="gp2")
    parser.add_argument("--cache-size", default="80Gi")
    parser.add_argument("--image", default="nvidia/cuda:12.8.1-devel-ubuntu22.04")
    parser.add_argument("--vllm-version", default="0.10.1+gptoss")
    parser.add_argument("--attention-backend", default="TRITON_ATTN_VLLM_V1")
    parser.add_argument("--max-model-len", type=int, default=4096)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.90)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--local-port", type=int, default=8000)
    parser.add_argument("--hf-token-secret", help="Optional Kubernetes secret containing Hugging Face token at key 'token'.")
    parser.add_argument("--scale-up", action="store_true")
    parser.add_argument("--scale-down", action="store_true")
    parser.add_argument("--port-forward", action="store_true", help="Keep kubectl port-forward running until Ctrl+C.")
    parser.add_argument("--write-client", default="artifacts/gpt-oss-20b/openai_client_example.py")
    parser.add_argument("--delete", action="store_true", help="Delete deployment/service/PVC instead of deploying.")
    args = parser.parse_args()

    if args.delete:
        run(["kubectl", "delete", "deployment,service,pvc", "-n", args.namespace, "-l", f"app={APP_LABEL}"], check=False)
        if args.scale_down:
            scale_nodegroup(args.region, args.cluster, args.nodegroup, desired=0)
        return

    if args.scale_up:
        print(f"Scaling {args.nodegroup} to desiredSize=1...")
        scale_nodegroup(args.region, args.cluster, args.nodegroup, desired=1)

    print("Waiting for a GPU node...")
    wait_for_gpu_node(args.selector, args.timeout)

    print("Applying Kubernetes resources...")
    kubectl_apply(resources_yaml(args))

    print("Waiting for vLLM rollout. First startup downloads the model and can take a while...")
    wait_for_rollout(args.namespace, args.timeout)

    cluster_base_url = f"http://{APP_LABEL}.{args.namespace}.svc.cluster.local:8000/v1"
    local_base_url = f"http://127.0.0.1:{args.local_port}/v1"
    write_client_example(Path(args.write_client), local_base_url)

    print(f"Cluster-internal OpenAI-compatible base_url: {cluster_base_url}")
    print(f"Local base_url after port-forward: {local_base_url}")
    print(f"Wrote client example: {Path(args.write_client).resolve()}")
    print()
    print("Manual port-forward command:")
    print(f"kubectl port-forward -n {args.namespace} svc/{APP_LABEL} {args.local_port}:8000")
    print()
    print("Then run:")
    print(f"python {args.write_client}")

    if args.port_forward:
        print("Starting port-forward and waiting for /health...")
        proc = wait_for_health(args.namespace, args.local_port, args.timeout)
        print(f"API ready: {local_base_url}")
        print("Press Ctrl+C to stop port-forward.")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()

    if args.scale_down:
        print("Not scaling down because the inference server is deployed. Use --delete --scale-down when done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
