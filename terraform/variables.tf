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
  description = "EKS Kubernetes version. Defaults to 1.32 to keep the AL2 bootstrap path available."
  type        = string
  default     = "1.32"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "gvisor_node_instance_types" {
  description = "EC2 instance types for the gVisor managed node group."
  type        = list(string)
  default     = ["m6i.large"]
}

variable "gvisor_desired_size" {
  description = "Desired gVisor node count."
  type        = number
  default     = 0
}

variable "gvisor_min_size" {
  description = "Minimum gVisor node count."
  type        = number
  default     = 0
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

variable "enable_smoke_test" {
  description = "Create a pod that runs with runtimeClassName gvisor."
  type        = bool
  default     = true
}

variable "enable_gpu_node_group" {
  description = "Create a separate GPU node group for non-gVisor GPU workloads."
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

variable "nvidia_device_plugin_image" {
  description = "NVIDIA Kubernetes device plugin image."
  type        = string
  default     = "nvcr.io/nvidia/k8s-device-plugin:v0.17.4"
}

variable "tags" {
  description = "Additional tags applied to AWS resources."
  type        = map(string)
  default     = {}
}
