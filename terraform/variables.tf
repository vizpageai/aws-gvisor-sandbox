variable "name" {
  description = "Base name for AWS and Kubernetes resources."
  type        = string
  default     = "gvisor-eks"
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "cluster_version" {
  description = "EKS Kubernetes control-plane and Ubuntu worker AMI version."
  type        = string
  default     = "1.36"
}

variable "ubuntu_release" {
  description = "Canonical Ubuntu LTS release used by CPU and GPU EKS worker nodes."
  type        = string
  default     = "24.04"

  validation {
    condition     = contains(["24.04"], var.ubuntu_release)
    error_message = "This platform currently validates Ubuntu 24.04 LTS worker images."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "IPv4 CIDRs allowed to reach the public EKS API endpoint. Use the deploy helper to supply the caller's current /32."
  type        = list(string)

  validation {
    condition = (
      length(var.cluster_endpoint_public_access_cidrs) > 0 &&
      !contains(var.cluster_endpoint_public_access_cidrs, "0.0.0.0/0")
    )
    error_message = "Provide at least one restricted CIDR; 0.0.0.0/0 is intentionally rejected."
  }
}

variable "high_availability_nat_gateway" {
  description = "Create one NAT gateway per Availability Zone. Disable only for lower-cost development deployments."
  type        = bool
  default     = true
}

variable "gvisor_node_instance_types" {
  description = "EC2 instance types for the gVisor managed node group."
  type        = list(string)
  default     = ["m6i.large"]
}

variable "gvisor_desired_size" {
  description = "Desired gVisor node count."
  type        = number
  default     = 1
}

variable "gvisor_min_size" {
  description = "Minimum gVisor node count."
  type        = number
  default     = 1
}

variable "gvisor_max_size" {
  description = "Maximum gVisor node count."
  type        = number
  default     = 4
}

variable "gvisor_release_channel" {
  description = "gVisor release channel path segment used by the public release bucket."
  type        = string
  default     = "release"

  validation {
    condition     = contains(["release", "nightly", "master"], var.gvisor_release_channel)
    error_message = "Use one of: release, nightly, master."
  }
}

variable "gvisor_release_version" {
  description = "Pinned gVisor release directory. Set to latest only for development environments."
  type        = string
  default     = "20260427.0"

  validation {
    condition     = can(regex("^(latest|[0-9]{8}\\.[0-9]+)$", var.gvisor_release_version))
    error_message = "Use latest or a dated gVisor release such as 20260427.0."
  }
}

variable "enable_smoke_test" {
  description = "Create a pod that runs with runtimeClassName gvisor."
  type        = bool
  default     = true
}

variable "enable_gpu_node_group" {
  description = "Create a separate GPU node group with gVisor nvproxy support."
  type        = bool
  default     = true
}

variable "gpu_node_instance_types" {
  description = "EC2 instance types for the GPU node group. g6 instances provide NVIDIA L4 GPUs."
  type        = list(string)
  default     = ["g6.xlarge"]
}

variable "gpu_desired_size" {
  description = "Desired GPU node count."
  type        = number
  default     = 0
}

variable "gpu_min_size" {
  description = "Minimum GPU node count."
  type        = number
  default     = 0
}

variable "gpu_max_size" {
  description = "Maximum GPU node count."
  type        = number
  default     = 1
}

variable "gpu_accelerator_label" {
  description = "Accelerator label applied to GPU nodes."
  type        = string
  default     = "nvidia-l4"
}

variable "gpu_operator_chart_version" {
  description = "NVIDIA GPU Operator chart version used to install the driver, container toolkit, and device plugin on Ubuntu GPU nodes."
  type        = string
  default     = "v26.3.3"
}

variable "gpu_driver_version" {
  description = "NVIDIA driver version pinned to an ABI supported by the selected gVisor nvproxy release."
  type        = string
  default     = "590.48.01"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.gpu_driver_version))
    error_message = "gpu_driver_version must be an exact NVIDIA driver version such as 590.48.01."
  }
}

variable "enable_cluster_autoscaler" {
  description = "Install Kubernetes Cluster Autoscaler with AWS managed-node-group auto-discovery."
  type        = bool
  default     = true
}

variable "cluster_autoscaler_chart_version" {
  description = "Cluster Autoscaler Helm chart version. Its app version must match the EKS Kubernetes minor version."
  type        = string
  default     = "9.59.0"
}

variable "cluster_autoscaler_image_tag" {
  description = "Cluster Autoscaler image tag; its minor version must match cluster_version."
  type        = string
  default     = "v1.36.1"
}

variable "sandbox_namespace" {
  description = "Kubernetes namespace for reusable sandbox pods and storage."
  type        = string
  default     = "default"
}

variable "sandbox_service_account_name" {
  description = "Service account used by reusable AWS sandbox pods."
  type        = string
  default     = "gvisor-sandbox"
}

variable "enable_sandbox_s3" {
  description = "Create an S3 bucket and IAM permissions for sandbox object storage."
  type        = bool
  default     = true
}

variable "sandbox_s3_bucket_name" {
  description = "Optional explicit S3 bucket name for sandbox object storage. Defaults to a region/account-scoped name."
  type        = string
  default     = null
}

variable "enable_ebs_csi" {
  description = "Install the AWS EBS CSI driver add-on and create a gp3 StorageClass for sandbox block storage."
  type        = bool
  default     = true
}

variable "sandbox_block_storage_class" {
  description = "StorageClass name used for AWS sandbox EBS-backed PVCs."
  type        = string
  default     = "gp3"
}

variable "tags" {
  description = "Additional tags applied to AWS resources."
  type        = map(string)
  default     = {}
}
