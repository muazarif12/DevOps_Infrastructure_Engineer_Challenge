#!/usr/bin/env python3
"""Thin S3 helper shared by backup.sh and restore.sh.

Uses boto3 against whatever AWS_ENDPOINT_URL is set to — MinIO locally (see docker-compose.yml),
a real S3 bucket once cloud credentials are available. Deliberately not the `aws` CLI: it would
need its own apt package, boto3 is one line and already vendors credential/endpoint handling that
works identically against both targets.
"""

from __future__ import annotations

import argparse
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def _client():
    # docker-compose.yml always sets AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_ENDPOINT_URL
    # as env vars, but leaves them as empty strings outside the local-s3 (MinIO) profile. An
    # empty string is still "present" to botocore's env-var credential provider, which would
    # try — and fail — to authenticate with blank keys instead of falling through to the EC2
    # instance role on the real AWS deployment. Strip empties so that fallback actually happens.
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL"):
        if not os.environ.get(key):
            os.environ.pop(key, None)

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
        config=Config(signature_version="s3v4"),
    )


def _ensure_bucket(client, bucket: str) -> None:
    """Create the bucket if it doesn't exist yet. Self-healing rather than depending on the
    separate `minio-init` compose service, which only runs under the local-s3 profile — a hard
    dependency on it from `backup` would break `docker compose up` whenever that profile isn't
    active (e.g. the AWS deployment, where the bucket already exists via Terraform and this
    call is just a no-op head_bucket check)."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code") or str(
            exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", "")
        )
        if code not in ("404", "NoSuchBucket"):
            raise
        client.create_bucket(Bucket=bucket)


def cmd_upload(args: argparse.Namespace) -> int:
    client = _client()
    _ensure_bucket(client, args.bucket)
    client.upload_file(args.file, args.bucket, args.key)
    print(f"uploaded {args.file} -> s3://{args.bucket}/{args.key}")
    return 0


def cmd_latest_key(args: argparse.Namespace) -> int:
    """Print the key of the most recent object under `prefix` (by lexicographic order — keys
    are timestamped as backups/<ISO8601>.sql.gz, so lexicographic sort == chronological)."""
    client = _client()
    paginator = client.get_paginator("list_objects_v2")
    latest: str | None = None
    for page in paginator.paginate(Bucket=args.bucket, Prefix=args.prefix):
        for obj in page.get("Contents", []):
            if latest is None or obj["Key"] > latest:
                latest = obj["Key"]
    if latest is None:
        print(f"no objects found under s3://{args.bucket}/{args.prefix}", file=sys.stderr)
        return 1
    print(latest)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    _client().download_file(args.bucket, args.key, args.file)
    print(f"downloaded s3://{args.bucket}/{args.key} -> {args.file}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("upload")
    up.add_argument("--bucket", required=True)
    up.add_argument("--key", required=True)
    up.add_argument("--file", required=True)
    up.set_defaults(func=cmd_upload)

    latest = sub.add_parser("latest-key")
    latest.add_argument("--bucket", required=True)
    latest.add_argument("--prefix", required=True)
    latest.set_defaults(func=cmd_latest_key)

    down = sub.add_parser("download")
    down.add_argument("--bucket", required=True)
    down.add_argument("--key", required=True)
    down.add_argument("--file", required=True)
    down.set_defaults(func=cmd_download)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
