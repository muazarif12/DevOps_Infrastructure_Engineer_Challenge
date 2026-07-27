#cloud-config
# Renders via Terraform's templatefile() in main.tf. Runs once on first boot; after that,
# Docker's own `restart: unless-stopped` policy (see docker-compose.yml) brings the stack back
# up on a plain reboot without cloud-init needing to run again.
package_update: true
package_upgrade: true

packages:
  - ca-certificates
  - curl
  - gnupg
  - git
  - awscli

runcmd:
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  - chmod a+r /etc/apt/keyrings/docker.asc
  - |
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list
  - apt-get update
  - apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  - systemctl enable --now docker

  - git clone --depth 1 ${git_repo_url} /opt/patient-intake-voice

  # Pull every secret out of SSM Parameter Store (never baked into the AMI or this template's
  # rendered user-data, which AWS does retain — the values themselves live only in SSM and,
  # briefly, in this generated .env file on the instance's own disk) and assemble .env.
  # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_ENDPOINT_URL are deliberately NOT written:
  # boto3 falls back to this instance's IAM role (see aws_iam_role.host in main.tf) against
  # the real S3 bucket Terraform created, so `docker compose up` here runs WITHOUT
  # `--profile local-s3` — no MinIO on this box at all.
  - |
    set -euo pipefail
    get_param() {
      aws ssm get-parameter --region ${aws_region} --with-decryption \
        --name "$1" --query 'Parameter.Value' --output text
    }
    ENV_FILE=/opt/patient-intake-voice/.env
    {
      echo "LIVEKIT_URL=$(get_param ${ssm_prefix}/livekit_url)"
      echo "LIVEKIT_API_KEY=$(get_param ${ssm_prefix}/livekit_api_key)"
      echo "LIVEKIT_API_SECRET=$(get_param ${ssm_prefix}/livekit_api_secret)"
      echo "LIVEKIT_LOG_LEVEL=INFO"
      echo "POSTGRES_DB=intake"
      echo "POSTGRES_USER=intake"
      echo "POSTGRES_PASSWORD=$(get_param ${ssm_prefix}/postgres_password)"
      echo "BACKUP_BUCKET=${backup_bucket}"
      echo "GRAFANA_ADMIN_PASSWORD=$(get_param ${ssm_prefix}/grafana_admin_password)"
      echo "PUBLIC_DOMAIN=${public_domain}"
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"

  - cd /opt/patient-intake-voice && docker compose up -d --build
