# Single EC2 host running the same docker-compose.yml stack used for local review — see
# README.md for why single-host is the deliberate choice at this call volume, not an oversight.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

# --- Networking ------------------------------------------------------------------------------

resource "aws_security_group" "host" {
  name        = "patient-intake-voice-${var.environment}"
  description = "Patient intake voice agent host: HTTP/HTTPS in, everything else closed except optional SSH."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (Caddy issues redirects/ACME challenges here)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS (patient-record API + Grafana sub-path, via Caddy)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = var.ssh_key_name != null ? [1] : []
    content {
      description = "SSH (emergency access only — SSM Session Manager is the normal path)"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [var.allowed_ssh_cidr]
    }
  }

  egress {
    description = "All outbound — the worker dials LiveKit Cloud + LiveKit Inference over WSS/HTTPS, no inbound ingress required for calls at all"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- IAM: SSM Session Manager access + scoped SSM Parameter Store + S3 backup access --------

resource "aws_iam_role" "host" {
  name = "patient-intake-voice-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "read_app_parameters" {
  name = "read-app-parameters"
  role = aws_iam_role.host.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/patient-intake-voice/${var.environment}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = data.aws_kms_alias.ssm.target_key_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "backup_bucket_access" {
  name = "backup-bucket-access"
  role = aws_iam_role.host.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.backups.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.backups.arn}/*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "host" {
  name = "patient-intake-voice-${var.environment}"
  role = aws_iam_role.host.name
}

# --- S3: backup target -----------------------------------------------------------------------
# Same role scripts/backup.sh and scripts/restore.sh already talk to via MinIO locally — moving
# here is an .env change (drop AWS_ENDPOINT_URL/static keys, rely on the instance role above),
# not a code change.

resource "aws_s3_bucket" "backups" {
  bucket = var.backup_bucket_name
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    filter {
      prefix = "backups/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    expiration {
      days = 90
    }
  }
}

# --- SSM Parameter Store: secrets, never in the AMI/user-data logs/this repo -----------------

locals {
  ssm_prefix = "/patient-intake-voice/${var.environment}"
  ssm_secrets = {
    "livekit_url"            = var.livekit_url
    "livekit_api_key"        = var.livekit_api_key
    "livekit_api_secret"     = var.livekit_api_secret
    "postgres_password"      = var.postgres_password
    "api_key"                = var.api_key
    "grafana_admin_password" = var.grafana_admin_password
  }
}

resource "aws_ssm_parameter" "secrets" {
  for_each = local.ssm_secrets

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "SecureString"
  value = each.value
}

# --- Compute -----------------------------------------------------------------------------------

resource "aws_instance" "host" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.host.id]
  iam_instance_profile   = aws_iam_instance_profile.host.name
  key_name               = var.ssh_key_name

  root_block_device {
    volume_size = var.root_volume_size_gb
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tpl", {
    git_repo_url  = var.git_repo_url
    ssm_prefix    = local.ssm_prefix
    aws_region    = var.aws_region
    public_domain = var.public_domain
    backup_bucket = aws_s3_bucket.backups.bucket
  })

  tags = {
    Name = "patient-intake-voice-${var.environment}"
  }
}

resource "aws_eip" "host" {
  instance = aws_instance.host.id
  domain   = "vpc"
}

# --- DNS (optional — see manage_dns/route53_zone_id in variables.tf) -------------------------

resource "aws_route53_record" "app" {
  count = var.manage_dns ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.public_domain
  type    = "A"
  ttl     = 60
  records = [aws_eip.host.public_ip]
}
