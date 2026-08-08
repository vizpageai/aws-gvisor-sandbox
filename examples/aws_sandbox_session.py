from __future__ import annotations

import os

from gvisor_sandbox import AwsSandbox, EBSBlockStorage, S3ObjectStorage

bucket = os.environ.get("SANDBOX_S3_BUCKET")

sandbox = AwsSandbox(
    name=os.environ.get("SANDBOX_NAME", "agent-dev-sandbox"),
    image=os.environ.get("SANDBOX_IMAGE", "python:3.12-slim"),
    s3=S3ObjectStorage(bucket=bucket, prefix="agent-dev-sandbox", region="us-east-1") if bucket else None,
    ebs=EBSBlockStorage(size_gib=8, storage_class_name="gp3", mount_path="/workspace"),
)

print("starting sandbox...")
print(sandbox.start(timeout_seconds=600))

print("writing persistent workspace file...")
print(
    sandbox.run_python(
        """
from pathlib import Path
import os

workspace = Path(os.environ["SANDBOX_WORKSPACE"])
workspace.mkdir(parents=True, exist_ok=True)
counter_file = workspace / "counter.txt"
counter = int(counter_file.read_text()) + 1 if counter_file.exists() else 1
counter_file.write_text(str(counter))

print("workspace:", workspace)
print("counter:", counter)
print("s3 uri:", os.environ.get("SANDBOX_S3_URI", "not configured"))
"""
    ).logs
)

print("stopping compute; EBS PVC and S3 objects are retained...")
sandbox.stop()

print("resuming sandbox...")
print(sandbox.start(timeout_seconds=600))

print("verifying EBS-backed file survived stop/resume...")
print(sandbox.exec(["sh", "-lc", "cat /workspace/counter.txt && echo"]))

if os.environ.get("SANDBOX_DELETE") == "1":
    print("deleting sandbox and PVC...")
    sandbox.delete(delete_storage=True)
else:
    print("leaving sandbox and PVC for inspection; set SANDBOX_DELETE=1 to delete them")
