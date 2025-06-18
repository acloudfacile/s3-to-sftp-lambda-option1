import os
import boto3
from datetime import datetime, timezone, timedelta

s3 = boto3.client("s3")
sns = boto3.client("sns")

S3_BUCKET = os.environ["S3_BUCKET"]
ARCHIVE_PREFIX = os.environ.get("ARCHIVE_PREFIX", "archived/")
SNS_TOPIC = os.environ["SNS_TOPIC"]
LOG_PREFIX = os.environ.get("LOG_PREFIX", "logs/")

def write_log_to_s3(log_lines):
    jst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    log_file_name = f"{LOG_PREFIX}file-watcher-log-{jst_now.strftime('%Y%m%d-%H%M%S')}.log"
    log_content = "\n".join(log_lines)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=log_file_name,
        Body=log_content.encode("utf-8")
    )
    return log_file_name

def lambda_handler(event, context):
    logs = []
    jst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    today_str = jst_now.strftime("%Y-%m-%d")
    archive_folder = f"{ARCHIVE_PREFIX}{today_str}/"
    logs.append(f"[{jst_now.strftime('%Y-%m-%d %H:%M:%S')}] Lambda started. Checking archive folder: {archive_folder}")

    # List objects in today's archive folder
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=archive_folder)
    found = False

    if "Contents" in resp:
        for obj in resp["Contents"]:
            key = obj["Key"]
            logs.append(f"Found archived file: {key}")
            if key.endswith("_iota.dat") or key.endswith("_iotaAfterMerge.dat"):
                logs.append(f"File '{key}' qualifies as today's file (OK).")
                found = True
                break

    if not found:
        subject = f"{S3_BUCKET} -<<File Check>>- No Files Exist"
        body = (
            "Dear Team\n\n"
            "No files exist in the source location. File yet to receive to AIG S3 Folder.\n\n"
            "Regards,\nTeam AIG"
        )
        logs.append(f"No qualifying file found in {archive_folder}. Sending SNS alert.")
        sns.publish(
            TopicArn=SNS_TOPIC,
            Subject=subject,
            Message=body
        )
        logs.append("SNS alert sent.")
        result = {"status": "File missing, email sent", "checked_at": jst_now.isoformat()}
    else:
        logs.append("Qualifying file found. No SNS alert sent.")
        result = {"status": "File found in archive, no action", "checked_at": jst_now.isoformat()}

    # Always write logs to S3 at the end
    log_file = write_log_to_s3(logs)
    result["log_file"] = log_file
    return result
