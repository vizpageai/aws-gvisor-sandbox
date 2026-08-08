resource "helm_release" "gpu_operator" {
  count = var.enable_gpu_node_group ? 1 : 0

  name             = "gpu-operator"
  namespace        = "gpu-operator"
  create_namespace = true
  repository       = "https://helm.ngc.nvidia.com/nvidia"
  chart            = "gpu-operator"
  version          = var.gpu_operator_chart_version
  wait             = true
  timeout          = 900

  set {
    name  = "driver.kernelModuleType"
    value = "auto"
  }

  depends_on = [aws_eks_node_group.gpu]
}
