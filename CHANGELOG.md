# Changelog

## 0.5.0 - Unreleased

- Run GPU sandboxes with gVisor nvproxy by default through a dedicated
  `gvisor-nvproxy` RuntimeClass.
- Install checksum-verified gVisor binaries on Ubuntu GPU nodes and pin the
  NVIDIA driver to an ABI supported by that gVisor release.
- Keep the native NVIDIA runtime as an explicit trusted-workload fallback.

This project follows Semantic Versioning.

## Unreleased

- Reorganized the README as a concise project landing page.
- Added an end-to-end user guide, safe teardown helpers, issue forms, a pull
  request template, and automated local documentation-link validation.
- Added an AWS CLI login-session credential bridge for Terraform deployment
  and upgrade scripts.

## 0.4.0 - 2026-08-06

- Added Kubernetes 1.36 and Canonical Ubuntu 24.04 worker support.
- Added NVIDIA GPU Operator and GPU scale-from-zero autoscaling.
- Added release automation, secure defaults, bounded jobs, and deployment
  verification.

## 0.3.0 - 2026-07-31

- Added the unified reusable CPU/GPU sandbox and Kubernetes Job API.
