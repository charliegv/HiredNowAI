# utils/s3_uploader.py

import boto3
import os
import uuid
import mimetypes


def upload_to_s3(local_path: str, folder: str, custom_filename: str | None = None) -> str:
    """
    Uploads any file to S3 under a custom folder.
    - If custom_filename is provided, it is used as the object key.
    - Uses UUID fallback otherwise.
    """

    bucket = os.getenv("AWS_S3_BUCKET")
    region = os.getenv("AWS_REGION", "eu-west-1")

    if not bucket:
        raise ValueError("AWS_S3_BUCKET env variable not set")

    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=region
    )

    ext = os.path.splitext(local_path)[1] or ""

    # If you provided a filename, use it — else fallback to UUID
    if custom_filename:
        filename = f"{folder}/{custom_filename}"
    else:
        filename = f"{folder}/{uuid.uuid4()}{ext}"

    # Infer content type
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    s3.upload_file(
        local_path,
        bucket,
        filename,
        ExtraArgs={"ContentType": content_type}
    )

    return f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"
