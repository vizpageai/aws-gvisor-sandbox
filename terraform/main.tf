locals {
  tags = merge(
    {
      Project = var.name
    },
    var.tags
  )
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ssm_parameter" "eks_al2_ami" {
  name = "/aws/service/eks/optimized-ami/${var.cluster_version}/amazon-linux-2/recommended/image_id"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = var.name
  cidr = var.vpc_cidr

  azs             = slice(data.aws_availability_zones.available.names, 0, 3)
  private_subnets = [cidrsubnet(var.vpc_cidr, 4, 0), cidrsubnet(var.vpc_cidr, 4, 1), cidrsubnet(var.vpc_cidr, 4, 2)]
  public_subnets  = [cidrsubnet(var.vpc_cidr, 4, 8), cidrsubnet(var.vpc_cidr, 4, 9), cidrsubnet(var.vpc_cidr, 4, 10)]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  enable_dns_support   = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }

  tags = local.tags
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.name
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true

  tags = local.tags
}

resource "aws_iam_role" "gvisor_node" {
  name = "${var.name}-gvisor-node"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "gvisor_node_worker" {
  role       = aws_iam_role.gvisor_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "gvisor_node_cni" {
  role       = aws_iam_role.gvisor_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "gvisor_node_registry" {
  role       = aws_iam_role.gvisor_node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role" "gpu_node" {
  count = var.enable_gpu_node_group ? 1 : 0
  name  = "${var.name}-gpu-node"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "gpu_node_worker" {
  count      = var.enable_gpu_node_group ? 1 : 0
  role       = aws_iam_role.gpu_node[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "gpu_node_cni" {
  count      = var.enable_gpu_node_group ? 1 : 0
  role       = aws_iam_role.gpu_node[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "gpu_node_registry" {
  count      = var.enable_gpu_node_group ? 1 : 0
  role       = aws_iam_role.gpu_node[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_launch_template" "gvisor_node" {
  name_prefix = "${var.name}-gvisor-"
  image_id    = data.aws_ssm_parameter.eks_al2_ami.value
  user_data = base64encode(templatefile("${path.module}/templates/gvisor-node-user-data.sh.tftpl", {
    cluster_name           = module.eks.cluster_name
    cluster_endpoint       = module.eks.cluster_endpoint
    cluster_ca             = module.eks.cluster_certificate_authority_data
    gvisor_release_channel = var.gvisor_release_channel
  }))

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = 40
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${var.name}-gvisor-node" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = local.tags
  }

  tags = local.tags
}

resource "aws_eks_node_group" "gvisor" {
  cluster_name    = module.eks.cluster_name
  node_group_name = "${var.name}-gvisor"
  node_role_arn   = aws_iam_role.gvisor_node.arn
  subnet_ids      = module.vpc.private_subnets

  instance_types = var.gvisor_node_instance_types
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = var.gvisor_desired_size
    min_size     = var.gvisor_min_size
    max_size     = var.gvisor_max_size
  }

  update_config {
    max_unavailable = 1
  }

  launch_template {
    id      = aws_launch_template.gvisor_node.id
    version = aws_launch_template.gvisor_node.latest_version
  }

  labels = {
    "runtime.gvisor.dev/enabled" = "true"
  }

  depends_on = [
    module.eks,
    aws_iam_role_policy_attachment.gvisor_node_worker,
    aws_iam_role_policy_attachment.gvisor_node_cni,
    aws_iam_role_policy_attachment.gvisor_node_registry
  ]

  tags = local.tags
}

resource "aws_eks_node_group" "gpu" {
  count = var.enable_gpu_node_group ? 1 : 0

  cluster_name    = module.eks.cluster_name
  node_group_name = "${var.name}-gpu"
  node_role_arn   = aws_iam_role.gpu_node[0].arn
  subnet_ids      = module.vpc.private_subnets

  ami_type       = "AL2_x86_64_GPU"
  instance_types = var.gpu_node_instance_types
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = var.gpu_desired_size
    min_size     = var.gpu_min_size
    max_size     = var.gpu_max_size
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    accelerator = var.gpu_accelerator_label
  }

  depends_on = [
    module.eks,
    aws_iam_role_policy_attachment.gpu_node_worker,
    aws_iam_role_policy_attachment.gpu_node_cni,
    aws_iam_role_policy_attachment.gpu_node_registry
  ]

  tags = local.tags
}

resource "kubernetes_runtime_class_v1" "gvisor" {
  metadata {
    name = "gvisor"
  }

  handler = "runsc"

  depends_on = [aws_eks_node_group.gvisor]
}

resource "kubernetes_pod_v1" "smoke" {
  count = var.enable_smoke_test ? 1 : 0

  metadata {
    name      = "gvisor-smoke"
    namespace = "default"
    labels = {
      app = "gvisor-smoke"
    }
  }

  spec {
    runtime_class_name = kubernetes_runtime_class_v1.gvisor.metadata[0].name
    restart_policy     = "Never"
    node_selector = {
      "runtime.gvisor.dev/enabled" = "true"
    }

    container {
      name    = "busybox"
      image   = "busybox:1.36"
      command = ["sh", "-c", "dmesg | head -n 20; tail -f /dev/null"]
    }
  }

  depends_on = [kubernetes_runtime_class_v1.gvisor]
}

resource "kubernetes_daemon_set_v1" "nvidia_device_plugin" {
  count = var.enable_gpu_node_group ? 1 : 0

  metadata {
    name      = "nvidia-device-plugin-daemonset"
    namespace = "kube-system"
    labels = {
      name = "nvidia-device-plugin-ds"
    }
  }

  spec {
    selector {
      match_labels = {
        name = "nvidia-device-plugin-ds"
      }
    }

    template {
      metadata {
        labels = {
          name = "nvidia-device-plugin-ds"
        }
      }

      spec {
        priority_class_name = "system-node-critical"

        node_selector = {
          accelerator = var.gpu_accelerator_label
        }

        toleration {
          key      = "nvidia.com/gpu"
          operator = "Exists"
          effect   = "NoSchedule"
        }

        container {
          name  = "nvidia-device-plugin-ctr"
          image = var.nvidia_device_plugin_image
          args  = ["--fail-on-init-error=false"]

          security_context {
            allow_privilege_escalation = false

            capabilities {
              drop = ["ALL"]
            }
          }

          volume_mount {
            name       = "device-plugin"
            mount_path = "/var/lib/kubelet/device-plugins"
          }
        }

        volume {
          name = "device-plugin"

          host_path {
            path = "/var/lib/kubelet/device-plugins"
          }
        }
      }
    }
  }

  depends_on = [aws_eks_node_group.gpu]
}
