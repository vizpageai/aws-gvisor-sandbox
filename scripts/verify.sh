#!/usr/bin/env bash
set -euo pipefail

kubectl get runtimeclass gvisor
kubectl get nodes -l runtime.gvisor.dev/enabled=true -o wide
kubectl get pod gvisor-smoke -o wide
kubectl logs gvisor-smoke

