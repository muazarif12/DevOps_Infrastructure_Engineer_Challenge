output "public_ip" {
  description = "Elastic IP. If manage_dns=false, point public_domain's DNS A record here manually."
  value       = aws_eip.host.public_ip
}

output "instance_id" {
  value = aws_instance.host.id
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "ssm_parameter_prefix" {
  value = local.ssm_prefix
}

output "ssm_session_command" {
  description = "Shell into the instance without SSH or a key pair, via SSM Session Manager (granted by the AmazonSSMManagedInstanceCore policy attached above)."
  value       = "aws ssm start-session --target ${aws_instance.host.id} --region ${var.aws_region}"
}
