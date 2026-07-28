output "region" {
  description = "AWS region."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API endpoint."
  value       = module.eks.cluster_endpoint
}

output "gvisor_node_group_name" {
  description = "Managed node group configured with runsc."
  value       = aws_eks_node_group.gvisor.node_group_name
}

output "gpu_node_group_name" {
  description = "Managed GPU node group for non-gVisor GPU workloads."
  value       = try(aws_eks_node_group.gpu[0].node_group_name, null)
}

output "runtime_class_name" {
  description = "Kubernetes RuntimeClass for gVisor workloads."
  value       = kubernetes_runtime_class_v1.gvisor.metadata[0].name
}

output "sandbox_service_account_name" {
  description = "Kubernetes service account used by AWS sandbox sessions."
  value       = kubernetes_service_account_v1.sandbox.metadata[0].name
}

output "sandbox_s3_bucket_name" {
  description = "S3 bucket used for sandbox object storage."
  value       = try(aws_s3_bucket.sandbox_objects[0].bucket, null)
}

output "sandbox_block_storage_class" {
  description = "StorageClass used for EBS-backed sandbox PVCs."
  value       = var.enable_ebs_csi ? kubernetes_storage_class_v1.gp3[0].metadata[0].name : null
}
