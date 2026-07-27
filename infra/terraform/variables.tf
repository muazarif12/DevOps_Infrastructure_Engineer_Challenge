variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Short environment name, used in tags and the SSM parameter path."
  type        = string
  default     = "prod"
}

variable "instance_type" {
  description = "EC2 instance size. t3.large (2 vCPU/8GB) covers the worker's ~2.6GB inference process plus Postgres/Prometheus/Loki/Grafana on the same host."
  type        = string
  default     = "t3.large"
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size. Postgres + Prometheus + Loki all accumulate data here."
  type        = number
  default     = 50
}

variable "ssh_key_name" {
  description = "Name of an existing EC2 key pair for emergency SSH access. Leave null to disable SSH entirely (SSM Session Manager, granted via the instance role below, is the preferred access path)."
  type        = string
  default     = null
}

variable "allowed_ssh_cidr" {
  description = "CIDR allowed to reach port 22, if ssh_key_name is set. Never leave this as 0.0.0.0/0 in a real deployment — scope it to a known office/VPN range."
  type        = string
  default     = "0.0.0.0/0"
}

variable "public_domain" {
  description = "Hostname to serve the API/Grafana on (Caddy's automatic-HTTPS domain). Matches PUBLIC_DOMAIN in .env. e.g. candidate-name.stratus-eval.dev"
  type        = string
}

variable "manage_dns" {
  description = "Whether Terraform should create the Route53 A record for public_domain. Set false (and point the record at the EIP output manually) if the hosted zone lives in an account/credentials this Terraform run doesn't have access to."
  type        = bool
  default     = false
}

variable "route53_zone_id" {
  description = "Hosted zone id to create the A record in. Required if manage_dns is true."
  type        = string
  default     = null
}

variable "backup_bucket_name" {
  description = "Globally-unique S3 bucket name for database backups. Bucket names collide across all of AWS, not just this account, so the default below is unlikely to be free — override it."
  type        = string
  default     = "patient-intake-voice-backups"
}

variable "git_repo_url" {
  description = "Repository the instance's cloud-init pulls at boot to build/run the compose stack."
  type        = string
}

# --- Secrets -------------------------------------------------------------------------------
# Passed in via a .tfvars file that is NEVER committed (see .gitignore) — e.g.
# `terraform apply -var-file=secrets.auto.tfvars`, or supplied by CI as TF_VAR_* env vars.
# Terraform writes these into SSM Parameter Store (SecureString); cloud-init reads them back
# at boot to build the instance's .env — they are never baked into the AMI, user-data logs,
# or this repo.

variable "livekit_url" {
  type      = string
  sensitive = true
}

variable "livekit_api_key" {
  type      = string
  sensitive = true
}

variable "livekit_api_secret" {
  type      = string
  sensitive = true
}

variable "postgres_password" {
  type      = string
  sensitive = true
}

variable "grafana_admin_password" {
  type      = string
  sensitive = true
}
