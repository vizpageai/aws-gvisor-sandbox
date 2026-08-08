data "aws_iam_policy_document" "cluster_autoscaler_assume_role" {
  count = var.enable_cluster_autoscaler ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:kube-system:cluster-autoscaler"]
    }
  }
}

data "aws_iam_policy_document" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler ? 1 : 0

  statement {
    sid    = "ReadScalingState"
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeLaunchConfigurations",
      "autoscaling:DescribeScalingActivities",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeLaunchTemplateVersions",
      "ec2:GetInstanceTypesFromInstanceRequirements",
      "eks:DescribeNodegroup",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ScaleDiscoveredNodeGroups"
    effect = "Allow"
    actions = [
      "autoscaling:SetDesiredCapacity",
      "autoscaling:TerminateInstanceInAutoScalingGroup",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/k8s.io/cluster-autoscaler/enabled"
      values   = ["true"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/k8s.io/cluster-autoscaler/${var.name}"
      values   = ["owned"]
    }
  }
}

resource "aws_iam_role" "cluster_autoscaler" {
  count              = var.enable_cluster_autoscaler ? 1 : 0
  name               = "${var.name}-cluster-autoscaler"
  assume_role_policy = data.aws_iam_policy_document.cluster_autoscaler_assume_role[0].json
  tags               = local.tags
}

resource "aws_iam_policy" "cluster_autoscaler" {
  count  = var.enable_cluster_autoscaler ? 1 : 0
  name   = "${var.name}-cluster-autoscaler"
  policy = data.aws_iam_policy_document.cluster_autoscaler[0].json
  tags   = local.tags
}

resource "aws_iam_role_policy_attachment" "cluster_autoscaler" {
  count      = var.enable_cluster_autoscaler ? 1 : 0
  role       = aws_iam_role.cluster_autoscaler[0].name
  policy_arn = aws_iam_policy.cluster_autoscaler[0].arn
}

resource "kubernetes_service_account_v1" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler ? 1 : 0

  metadata {
    name      = "cluster-autoscaler"
    namespace = "kube-system"
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.cluster_autoscaler[0].arn
    }
  }
}

# EKS managed-node-group tags do not propagate to the backing ASGs, so tag the
# ASGs directly for Cluster Autoscaler discovery and scale-from-zero templates.
resource "aws_autoscaling_group_tag" "gvisor_autoscaler_enabled" {
  count                  = var.enable_cluster_autoscaler ? 1 : 0
  autoscaling_group_name = aws_eks_node_group.gvisor.resources[0].autoscaling_groups[0].name
  tag {
    key                 = "k8s.io/cluster-autoscaler/enabled"
    value               = "true"
    propagate_at_launch = false
  }
}

resource "aws_autoscaling_group_tag" "gvisor_autoscaler_cluster" {
  count                  = var.enable_cluster_autoscaler ? 1 : 0
  autoscaling_group_name = aws_eks_node_group.gvisor.resources[0].autoscaling_groups[0].name
  tag {
    key                 = "k8s.io/cluster-autoscaler/${var.name}"
    value               = "owned"
    propagate_at_launch = false
  }
}

resource "aws_autoscaling_group_tag" "gvisor_node_template_label" {
  count                  = var.enable_cluster_autoscaler ? 1 : 0
  autoscaling_group_name = aws_eks_node_group.gvisor.resources[0].autoscaling_groups[0].name
  tag {
    key                 = "k8s.io/cluster-autoscaler/node-template/label/runtime.gvisor.dev/enabled"
    value               = "true"
    propagate_at_launch = false
  }
}

resource "aws_autoscaling_group_tag" "gpu_autoscaler_enabled" {
  count                  = var.enable_cluster_autoscaler && var.enable_gpu_node_group ? 1 : 0
  autoscaling_group_name = aws_eks_node_group.gpu[0].resources[0].autoscaling_groups[0].name
  tag {
    key                 = "k8s.io/cluster-autoscaler/enabled"
    value               = "true"
    propagate_at_launch = false
  }
}

resource "aws_autoscaling_group_tag" "gpu_autoscaler_cluster" {
  count                  = var.enable_cluster_autoscaler && var.enable_gpu_node_group ? 1 : 0
  autoscaling_group_name = aws_eks_node_group.gpu[0].resources[0].autoscaling_groups[0].name
  tag {
    key                 = "k8s.io/cluster-autoscaler/${var.name}"
    value               = "owned"
    propagate_at_launch = false
  }
}

resource "aws_autoscaling_group_tag" "gpu_node_template_label" {
  count                  = var.enable_cluster_autoscaler && var.enable_gpu_node_group ? 1 : 0
  autoscaling_group_name = aws_eks_node_group.gpu[0].resources[0].autoscaling_groups[0].name
  tag {
    key                 = "k8s.io/cluster-autoscaler/node-template/label/accelerator"
    value               = var.gpu_accelerator_label
    propagate_at_launch = false
  }
}

resource "helm_release" "cluster_autoscaler" {
  count = var.enable_cluster_autoscaler ? 1 : 0

  name       = "cluster-autoscaler"
  namespace  = "kube-system"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = var.cluster_autoscaler_chart_version

  set {
    name  = "cloudProvider"
    value = "aws"
  }

  set {
    name  = "awsRegion"
    value = var.region
  }

  set {
    name  = "autoDiscovery.clusterName"
    value = module.eks.cluster_name
  }

  set {
    name  = "image.tag"
    value = var.cluster_autoscaler_image_tag
  }

  set {
    name  = "rbac.serviceAccount.create"
    value = "false"
  }

  set {
    name  = "rbac.serviceAccount.name"
    value = kubernetes_service_account_v1.cluster_autoscaler[0].metadata[0].name
  }

  set {
    name  = "nodeSelector.runtime\\.gvisor\\.dev/enabled"
    value = "true"
  }

  depends_on = [
    aws_iam_role_policy_attachment.cluster_autoscaler,
    aws_autoscaling_group_tag.gvisor_autoscaler_enabled,
    aws_autoscaling_group_tag.gvisor_autoscaler_cluster,
    aws_autoscaling_group_tag.gvisor_node_template_label,
    aws_autoscaling_group_tag.gpu_autoscaler_enabled,
    aws_autoscaling_group_tag.gpu_autoscaler_cluster,
    aws_autoscaling_group_tag.gpu_node_template_label,
  ]

  lifecycle {
    precondition {
      condition     = var.gvisor_min_size >= 1
      error_message = "Cluster Autoscaler is pinned to the gVisor node pool, so gvisor_min_size must be at least 1 when autoscaling is enabled."
    }

    precondition {
      condition     = startswith(trimprefix(var.cluster_autoscaler_image_tag, "v"), "${var.cluster_version}.")
      error_message = "cluster_autoscaler_image_tag must have the same Kubernetes minor version as cluster_version."
    }
  }
}
