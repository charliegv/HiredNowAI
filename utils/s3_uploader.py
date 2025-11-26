# utils/s3_uploader.py

import boto3
import os
import uuid


def upload_to_s3(local_path: str) -> str:
    """
    Uploads a local file to S3 and returns its public URL.
    Designed for buckets with ACLs disabled (Object Ownership = Bucket Owner Enforced).
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

    filename = f"cv-variants/{uuid.uuid4()}.docx"

    try:
        # No ACL here - bucket owner enforced mode forbids ACLs
        s3.upload_file(
            local_path,
            bucket,
            filename,
            ExtraArgs={
                "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            }
        )
    except Exception as e:
        raise Exception(f"Failed to upload file to S3: {str(e)}")

    return f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"
