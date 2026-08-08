from __future__ import annotations

import os

from gvisor_sandbox import EBSBlockStorage, S3ObjectStorage, SandboxPlatform, SandboxSpec

platform = SandboxPlatform(namespace=os.getenv("SANDBOX_NAMESPACE", "default"))
name = os.getenv("SANDBOX_NAME", "agent-quickstart")
s3_uri = os.getenv("SANDBOX_S3_URI")

spec = SandboxSpec(
    image="python:3.12-slim",
    s3=S3ObjectStorage.from_uri(s3_uri, region=os.getenv("AWS_REGION", "us-east-1")) if s3_uri else None,
    ebs=EBSBlockStorage(size_gib=8, storage_class_name="gp3", mount_path="/workspace"),
    env={"AGENT_NAME": name},
)

sandbox = platform.create(name, spec, timeout_seconds=900)
result = sandbox.run_python(
    """
from pathlib import Path
import os

workspace = Path(os.environ["SANDBOX_WORKSPACE"])
counter_file = workspace / "runs.txt"
count = int(counter_file.read_text()) + 1 if counter_file.exists() else 1
counter_file.write_text(str(count))
print(f"agent={os.environ['AGENT_NAME']} persistent_run={count}")
print(f"s3={os.getenv('SANDBOX_S3_URI', 'not configured')}")
"""
)
print(result.output)

# stop() releases compute but leaves the Deployment configuration and EBS data.
sandbox.stop()
print("stopped; call sandbox.start() to resume, or sandbox.delete(delete_storage=True) to remove everything")
