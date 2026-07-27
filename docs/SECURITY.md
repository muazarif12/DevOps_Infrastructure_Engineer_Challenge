# Security & HIPAA Posture

Honest inventory of what handles PHI, what's implemented, and what's explicitly deferred. This
is not a compliance certification — it's the awareness the challenge brief asks for ("mention
what you would do in production... even if you don't implement it all").

## What counts as PHI here

Every row in `patient_records` (`app/store.py`) is PHI in aggregate. Mapped to HIPAA Safe
Harbor identifiers (§164.514(b)(2)):

| Field(s) | Identifier category |
|---|---|
| `given_name`, `family_name`, `contact_name` | (A) Names |
| `street`, `unit`, `city`, `postal_code` | (B) Geographic subdivision smaller than a state |
| `birth_date`, `created_at`, `updated_at`, `archived_at` | (C) Dates (DOB explicitly; record dates in context) |
| `phone`, `contact_phone` | (D) Telephone numbers |
| `email` | (F) Email address |
| `member_id` | (H) Health plan beneficiary number |
| `record_id` | (R) Unique identifying number/code |
| `insurer`, `sex`, `language` | PHI in context, not standalone identifiers |

PHI also exists **outside the database**, in-flight, during every call:
- Audio and transcripts pass through LiveKit Cloud, and via LiveKit Inference to Deepgram
  (STT), OpenAI (LLM), Cartesia (TTS), and ai-coustics (noise cancellation).
- The full spoken intake (name, DOB, address, insurance) lives in the LLM's chat context for
  the duration of the call (`app/flow.py`, `chat_ctx=self.chat_ctx` passed between stages).
- **DEBUG-level logging leaks PHI**: the LiveKit framework logs tool call arguments/results at
  DEBUG, which include names, DOB, addresses. Every environment in this repo runs at `INFO` —
  do not lower this in production without separately handling log-level PHI exposure.

**Subprocessors that would need a BAA before real patient traffic:** LiveKit, Deepgram, OpenAI,
Cartesia, ai-coustics. None of this is negotiated or verified here — flagging it is the point.

## Implemented

- **API authentication** (`app/web.py`) — every `/patients*` route requires `X-API-Key`,
  checked with `secrets.compare_digest` (constant-time) and fails closed if `API_KEY` isn't
  configured at all, rather than silently running open.
- **Encrypted secrets at rest for the AWS path** — Terraform stores every credential in SSM
  Parameter Store as `SecureString` (`infra/terraform/main.tf`), scoped IAM read access to
  exactly `/patient-intake-voice/<env>/*`, never in the AMI or committed anywhere.
- **No static cloud credentials on the instance** — the EC2 instance role
  (`aws_iam_role.host`) grants scoped S3 read/write to just the backup bucket; `scripts/
  backup.sh`/`restore.sh` never carry a long-lived AWS key on that path (only locally, for
  MinIO, where root credentials are inherent to running your own object store).
- **Encryption at rest**: EBS root volume (`encrypted = true`), S3 backups
  (SSE-KMS, `aws_s3_bucket_server_side_encryption_configuration`), SSM parameters
  (`SecureString`, KMS-backed).
- **Network exposure minimized**: Prometheus/Alertmanager/MinIO bind to `127.0.0.1` in
  `docker-compose.yml`, not the public interface; the security group
  (`aws_security_group.host`) only opens 80/443 (+ optional 22 to a specific CIDR, never
  0.0.0.0/0 for SSH by default reasoning — though the variable technically defaults open, see
  below). SSM Session Manager is the intended access path, not SSH.
- **TLS termination** at Caddy, with automatic Let's Encrypt once `PUBLIC_DOMAIN` is a real,
  DNS-resolvable hostname.
- **No PHI in the container image**: `.dockerignore` excludes `.env*`, `*.db`, `.git/`.
- **Structured, correlatable logs** without needing to log PHI fields directly — the `room`/
  `request_id` correlation (`app/worker.py`, `app/obs.py`) means an operator can trace a call or
  request without a separate PHI-bearing audit column.

## Explicitly deferred (and what real HIPAA readiness would need)

| Gap | HIPAA reference | What's needed |
|---|---|---|
| No audit log of who read/changed a record | §164.312(b) — Audit controls | A dedicated, append-only audit table (who, what, when) for every read/write, separate from application logs which aren't tamper-evident or access-controlled the same way. |
| No encryption-in-transit enforcement inside the docker network | §164.312(e) | Postgres connections between `api`/`worker` and `postgres` are plaintext on the internal Docker network today — acceptable only because that network isn't reachable from outside the host. A multi-host deployment would need TLS between services. |
| No real user auth (single shared API key) | §164.312(d) — Person/entity authentication | A single static key means no per-user accountability; real deployment needs per-staff-member credentials (OAuth/OIDC) so "who accessed this record" is answerable. |
| No formal data retention/deletion policy | §164.310(d)(2)(i) | `DELETE /patients/{id}` is a soft delete (`archived_at`) — the row, and its PHI, is retained forever. A real policy needs an actual purge process and a defined retention period. |
| No BAAs in place | 45 CFR §164.502(e) | LiveKit/Deepgram/OpenAI/Cartesia/ai-coustics all need executed BAAs before real PHI flows through them. |
| `allowed_ssh_cidr` defaults to `0.0.0.0/0` | — | Terraform variable default is permissive for first-run convenience; **must** be scoped to a known range before `ssh_key_name` is actually set in any real deployment. |
| Alerting has no external delivery | — | Scoped choice for this exercise (Alertmanager UI only) — a real on-call rotation needs Slack/PagerDuty wired to `infra/observability/alertmanager.yml`. |
| DB backups aren't encrypted client-side before upload | — | SSE-KMS on the bucket protects data at rest on AWS's side; a defense-in-depth posture would also encrypt the dump itself before it leaves the host. |

## Attack surface, briefly

- **Public**: ports 80/443 (Caddy → API + Grafana sub-path only). Grafana itself still has its
  own login (admin password from SSM/`.env`) behind that.
- **Not public**: Prometheus, Alertmanager, MinIO console, Postgres, the worker's health/metrics
  port — all internal-network-only or `127.0.0.1`-bound.
- **Worker has no listening surface relevant to calls at all** — it's egress-only to LiveKit
  Cloud (WSS) and LiveKit Inference (HTTPS); there is no inbound port a caller's SIP session
  reaches on this host directly.
