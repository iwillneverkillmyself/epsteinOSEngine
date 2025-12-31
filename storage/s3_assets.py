from __future__ import annotations

import logging
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)

try:
    import boto3
except Exception:  # pragma: no cover
    boto3 = None


def _client():
    if boto3 is None:
        raise RuntimeError("boto3 is required for S3 asset serving but is not installed")
    kwargs = {}
    if Config.S3_REGION:
        kwargs["region_name"] = Config.S3_REGION
    return boto3.client("s3", **kwargs)


def presign_get(
    key: str,
    expires_seconds: Optional[int] = None,
    response_content_type: Optional[str] = None,
    response_content_disposition: Optional[str] = None,
) -> str:
    if not Config.S3_BUCKET:
        raise RuntimeError("S3_BUCKET is not configured")
    if expires_seconds is None:
        expires_seconds = Config.S3_PRESIGN_EXPIRES_SECONDS

    c = _client()
    params = {"Bucket": Config.S3_BUCKET, "Key": key}
    # Force correct content-type/disposition on download. Useful when objects were uploaded
    # with a generic content-type and some clients refuse to render inline.
    if response_content_type:
        params["ResponseContentType"] = response_content_type
    if response_content_disposition:
        params["ResponseContentDisposition"] = response_content_disposition
    return c.generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=int(expires_seconds),
    )




