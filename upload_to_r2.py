"""Upload large files to Cloudflare R2 with multipart uploads.

Required environment variables:
- CF_R2_ACCOUNT_ID
- CF_R2_ACCESS_KEY_ID
- CF_R2_SECRET_ACCESS_KEY
Optional:
- CF_R2_BUCKET (defaults to "mio")
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3

ACCOUNT_ID = os.environ.get("CF_R2_ACCOUNT_ID", "")
ACCESS_KEY_ID = os.environ.get("CF_R2_ACCESS_KEY_ID", "")
SECRET_KEY = os.environ.get("CF_R2_SECRET_ACCESS_KEY", "")
BUCKET = os.environ.get("CF_R2_BUCKET", "mio")

if not ACCOUNT_ID or not ACCESS_KEY_ID or not SECRET_KEY:
    raise SystemExit(
        "Missing R2 credentials. Set CF_R2_ACCOUNT_ID, CF_R2_ACCESS_KEY_ID, and "
        "CF_R2_SECRET_ACCESS_KEY before running this script."
    )

ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"

UPLOADS = [
    (r"dist\MioTranslator-Setup-v1.2.2.exe", "MioTranslator-Setup-v1.2.2.exe"),
    (r"dist\MioTranslator-Setup-v1.2.3_beta2.exe", "MioTranslator-Setup-v1.2.3_beta2.exe"),
]

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY_ID,
    aws_secret_access_key=SECRET_KEY,
    region_name="auto",
)

CHUNK = 256 * 1024 * 1024


def upload(local_path: str, key: str) -> None:
    path = Path(local_path)
    size = path.stat().st_size
    size_mb = size / 1024 / 1024
    print(f"\nUploading: {path.name} ({size_mb:.0f} MB) -> {BUCKET}/{key}")

    multipart = s3.create_multipart_upload(
        Bucket=BUCKET,
        Key=key,
        ContentType="application/octet-stream",
        ContentDisposition=f'attachment; filename="{path.name}"',
    )
    upload_id = multipart["UploadId"]
    parts: list[dict[str, object]] = []

    try:
        with path.open("rb") as handle:
            part_number = 0
            while True:
                data = handle.read(CHUNK)
                if not data:
                    break
                part_number += 1
                uploaded = (part_number - 1) * CHUNK + len(data)
                percent = uploaded / size * 100
                print(
                    f"  Part {part_number:3d}  {percent:5.1f}%  "
                    f"({uploaded / 1024 / 1024:.0f} / {size_mb:.0f} MB)",
                    end="\r",
                )
                response = s3.upload_part(
                    Bucket=BUCKET,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=data,
                )
                parts.append({"PartNumber": part_number, "ETag": response["ETag"]})

        s3.complete_multipart_upload(
            Bucket=BUCKET,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        print(f"\n  Done: {key}")
    except Exception:
        s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload_id)
        raise


if __name__ == "__main__":
    base = Path(__file__).parent
    for local, key in UPLOADS:
        upload(str(base / local), key)
    print("\nAll uploads completed.")