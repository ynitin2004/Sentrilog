import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from mypy_boto3_s3.client import S3Client

from .config import settings

_s3_client: S3Client | None = None


def get_s3_client() -> S3Client:
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


def get_object_bytes(key: str) -> bytes | None:
    """Returns the object's bytes, or None if it doesn't exist yet -- the intake API creates
    the document row and hands out a presigned URL before the client has actually uploaded
    anything (see services/intake/main.py), so "not there yet" is an expected, retryable
    state here, not an error.
    """
    client = get_s3_client()
    try:
        response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        return response["Body"].read()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise
