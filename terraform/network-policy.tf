resource "kubernetes_network_policy_v1" "sandbox_deny_ingress" {
  metadata {
    name      = "sandbox-deny-ingress"
    namespace = var.sandbox_namespace
  }

  spec {
    pod_selector {
      match_labels = {
        "sandbox.platform/managed" = "true"
      }
    }

    policy_types = ["Ingress"]
  }
}
