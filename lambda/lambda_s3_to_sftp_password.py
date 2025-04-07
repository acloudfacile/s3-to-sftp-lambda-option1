import os
import json
import boto3
import paramiko
import logging
import watchtower
from datetime import datetime, timezone, timedelta
from botocore.exceptions import ClientError

# --- Environment Variables --- #
S3_BUCKET = os.environ.get('S3_BUCKET')
S3_PREFIX = os.environ.get('S3_PREFIX', 'incoming/')
SECRET_NAME = os.environ.get('SECRET_NAME')
SNS_TOPIC = os.environ.get('SNS_TOPIC')
WEEKEND_ALERT = os.environ.get('WEEKEND_ALERT', 'true').lower() == 'true'

# --- CloudWatch Log Setup --- #
japan_now = datetime.now(timezone.utc) + timedelta(hours=9)
stream_name = japan_now.strftime("run-%Y%m%d-%H%M%S-JST")
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(
    watchtower.CloudWatchLogHandler(
        log_group='s3-windows-iota',
        stream_name=stream_name
    )
)

def send_sns(subject, message):
    try:
        boto3.client('sns').publish(
            TopicArn=SNS_TOPIC,
            Subject=subject,
            Message=message
        )
        logger.info("📨 SNS alert sent.")
    except Exception as e:
        logger.error(f"❌ Failed to send SNS alert: {str(e)}")

def lambda_handler(event, context):
    logger.info("🚀 Lambda triggered")

    now = datetime.now(timezone.utc) + timedelta(hours=9)

    # Weekend Notification
    if WEEKEND_ALERT and now.weekday() >= 5:
        send_sns("⚠️ SFTP File Alert - Weekend Upload", "A file was uploaded on Saturday or Sunday.")
        return

    s3 = boto3.client('s3')
    secrets = boto3.client('secretsmanager')

    try:
        for record in event['Records']:
            s3_key = record['s3']['object']['key']
            if not s3_key.endswith('.dat'):
                logger.info(f"⏩ Skipping non-dat file: {s3_key}")
                return

            filename = os.path.basename(s3_key)
            local_file = f"/tmp/{filename}"

            # Download from S3
            logger.info(f"⬇️ Downloading s3://{S3_BUCKET}/{s3_key}")
            s3.download_file(S3_BUCKET, s3_key, local_file)

            # Load secrets
            secret = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)['SecretString'])

            # SFTP Connection
            logger.info("🔐 Connecting to SFTP server")
            transport = paramiko.Transport((secret['host'], int(secret['port'])))
            transport.connect(username=secret['username'], password=secret['password'])
            sftp = paramiko.SFTPClient.from_transport(transport)

            # Upload to remote SFTP
            remote_incoming = os.path.join(secret['remote_path'], filename).replace("\\", "/")
            remote_archive = os.path.join(secret['archive_path'], filename).replace("\\", "/")
            sftp.put(local_file, remote_incoming)
            sftp.put(local_file, remote_archive)
            logger.info(f"✅ Uploaded to SFTP: {remote_incoming}, {remote_archive}")
            sftp.close()
            transport.close()

            # Archive to S3
            archive_prefix = now.strftime("archived/%Y-%m-%d/")
            archive_key = f"{archive_prefix}{filename}"
            logger.info(f"📦 Archiving to S3: {archive_key}")

            s3.copy_object(
                Bucket=S3_BUCKET,
                CopySource={'Bucket': S3_BUCKET, 'Key': s3_key},
                Key=archive_key
            )
            s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
            logger.info("🧹 Original file deleted from S3")

            # Notify Success
            send_sns("✅ SFTP Transfer Success", f"File `{filename}` successfully transferred and archived.")

    except Exception as e:
        logger.error(f"❌ Lambda error: {str(e)}")
        send_sns("❌ SFTP Transfer Failed", str(e))
        raise

    # Flush logs explicitly
    try:
        logger.handlers[0].flush()
        logger.info("🪵 Logs flushed to CloudWatch")
    except Exception as flush_err:
        logger.warning(f"⚠️ Log flush failed: {str(flush_err)}")