data "aws_caller_identity" "current" {}

locals {
  sandbox_s3_bucket_name = var.sandbox_s3_bucket_name != null ? var.sandbox_s3_bucket_name : "${var.name}-${data.aws_caller_identity.current.account_id}-${var.region}-sandboxes"
  oidc_provider_url      = replace(module.eks.oidc_provider_arn, "/^(.*provider/)/", "")
}

resource "aws_s3_bucket" "sandbox_objects" {
  count = var.enable_sandbox_s3 ? 1 : 0

  bucket = local.sandbox_s3_bucket_name
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "sandbox_objects" {
  count = var.enable_sandbox_s3 ? 1 : 0

  bucket                  = aws_s3_bucket.sandbox_objects[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sandbox_objects" {
  count = var.enable_sandbox_s3 ? 1 : 0

  bucket = aws_s3_bucket.sandbox_objects[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "sandbox_objects" {
  count = var.enable_sandbox_s3 ? 1 : 0

  bucket = aws_s3_bucket.sandbox_objects[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "sandbox_service_account_assume_role" {
  statement {
    effect = "Allow"

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
      values   = ["system:serviceaccount:${var.sandbox_namespace}:${var.sandbox_service_account_name}"]
    }
  }
}

data "aws_iam_policy_document" "sandbox_s3_access" {
  count = var.enable_sandbox_s3 ? 1 : 0

  statement {
    sid       = "ListSandboxBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.sandbox_objects[0].arn]
  }

  statement {
    sid    = "ReadWriteSandboxObjects"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.sandbox_objects[0].arn}/*"]
  }
}

resource "aws_iam_role" "sandbox_service_account" {
  name               = "${var.name}-sandbox-sa"
  assume_role_policy = data.aws_iam_policy_document.sandbox_service_account_assume_role.json
  tags               = local.tags
}

resource "aws_iam_policy" "sandbox_s3_access" {
  count = var.enable_sandbox_s3 ? 1 : 0

  name   = "${var.name}-sandbox-s3"
  policy = data.aws_iam_policy_document.sandbox_s3_access[0].json
  tags   = local.tags
}

resource "aws_iam_role_policy_attachment" "sandbox_s3_access" {
  count = var.enable_sandbox_s3 ? 1 : 0

  role       = aws_iam_role.sandbox_service_account.name
  policy_arn = aws_iam_policy.sandbox_s3_access[0].arn
}

resource "kubernetes_service_account_v1" "sandbox" {
  metadata {
    name      = var.sandbox_service_account_name
    namespace = var.sandbox_namespace
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.sandbox_service_account.arn
    }
  }
}

data "aws_iam_policy_document" "ebs_csi_assume_role" {
  statement {
    effect = "Allow"

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
      values   = ["system:serviceaccount:kube-system:ebs-csi-controller-sa"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  count = var.enable_ebs_csi ? 1 : 0

  name               = "${var.name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.ebs_csi_assume_role.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  count = var.enable_ebs_csi ? 1 : 0

  role       = aws_iam_role.ebs_csi[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_eks_addon" "ebs_csi" {
  count = var.enable_ebs_csi ? 1 : 0

  cluster_name             = module.eks.cluster_name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.ebs_csi[0].arn

  depends_on = [aws_iam_role_policy_attachment.ebs_csi]
  tags       = local.tags
}

resource "kubernetes_storage_class_v1" "gp3" {
  count = var.enable_ebs_csi ? 1 : 0

  metadata {
    name = var.sandbox_block_storage_class
  }

  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Delete"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true

  parameters = {
    type      = "gp3"
    encrypted = "true"
  }

  depends_on = [aws_eks_addon.ebs_csi]
}
