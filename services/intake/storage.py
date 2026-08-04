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


def presigned_put_url(key: str, content_type: str, expires_in: int = 900) -> str:
    # generate_presigned_url signs locally and makes no network call, so it's safe to call
    # synchronously from an async route handler.
    client = get_s3_client()
    url: str = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires_in,
    )
    return url


def object_exists(key: str) -> bool:
    client = get_s3_client()
    try:
        client.head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise
