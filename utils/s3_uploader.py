# utils/s3_uploader.py

import boto3
import os
import uuid
import mimetypes


def upload_to_s3(local_path: str, folder: str) -> str:
    """
    Uploads any file to S3 under a custom folder.
    Works with Object Ownership = Bucket Owner Enforced (no ACLs).
    Infers MIME type automatically.
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

    # Keep original extension
    ext = os.path.splitext(local_path)[1] or ""
    filename = f"{folder}/{uuid.uuid4()}{ext}"

    # Determine content type
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    try:
        s3.upload_file(
            local_path,
            bucket,
            filename,
            ExtraArgs={"ContentType": content_type}
        )
    except Exception as e:
        raise Exception(f"Failed to upload file to S3: {str(e)}")

    return f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"
