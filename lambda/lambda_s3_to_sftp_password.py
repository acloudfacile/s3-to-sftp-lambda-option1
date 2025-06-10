import os
import json
import boto3
import paramiko
import hashlib
from datetime import datetime, timezone, timedelta

# --- ENVIRONMENT VARIABLES --- #
S3_BUCKET = os.environ.get("S3_BUCKET")
SECRET_NAME = os.environ.get("SECRET_NAME")
SNS_TOPIC = os.environ.get("SNS_TOPIC")
LANDING_PREFIX = os.environ.get("LANDING_PREFIX", "aig-iota-landing-folder/")
STAGING_PREFIX = os.environ.get("STAGING_PREFIX", "staging/")
ARCHIVE_PREFIX = os.environ.get("ARCHIVE_PREFIX", "archived/")
LOG_PREFIX = os.environ.get("LOG_PREFIX", "logs/")

# --- LOGGER SETUP --- #
def setup_logger():
    jst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    path = f"/tmp/lambda-log-{jst_now.strftime('%Y%m%d-%H%M%S')}.log"
    open(path, 'a').close()
    return path

log_file_path = setup_logger()

def log_to_file(msg):
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(log_file_path, "a") as f:
        f.write(f"{timestamp} - {msg}\n")

def send_sns(subject, message):
    try:
        boto3.client("sns").publish(TopicArn=SNS_TOPIC, Subject=subject, Message=message)
        log_to_file("📨 SNS alert sent.")
    except Exception as e:
        log_to_file(f"❌ SNS failed: {e}")

def calculate_sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""): h.update(chunk)
    return h.hexdigest()

def merge_files(file1, file2, out_file):
    with open(out_file, "wb") as o:
        for path in [file1, file2]:
            with open(path, "rb") as f: o.write(f.read())
    log_to_file("🔀 Files merged.")
    checksum = calculate_sha256(out_file)
    log_to_file(f"🔐 SHA-256 of merged file: {checksum}")
    return checksum

def connect_sftp(secret):
    transport = paramiko.Transport((secret["host"], int(secret["port"])))
    transport.connect(username=secret["username"], password=secret["password"])
    return paramiko.SFTPClient.from_transport(transport), transport

# --- MAIN HANDLER --- #
def lambda_handler(event, context):
    now = datetime.now(timezone.utc) + timedelta(hours=9)
    s3 = boto3.client("s3")
    secrets = boto3.client("secretsmanager")

    try:
        for record in event["Records"]:
            key = record["s3"]["object"]["key"]
            filename = os.path.basename(key)
            if filename != "iota.dat":
                log_to_file(f"🚫 Invalid file: {key}")
                send_sns("Invalid File", f"{filename} rejected. Only iota.dat allowed.")
                continue

            local_file = f"/tmp/{filename}"
            s3.download_file(S3_BUCKET, key, local_file)
            log_to_file(f"⬇️ Downloaded: {key}")
            log_to_file(f"🔐 SHA-256: {calculate_sha256(local_file)}")
            log_to_file(f"📏 Size: {os.path.getsize(local_file)} bytes")

            archive_name = f"{now.strftime('%Y%m%d%H%M%S')}_iota.dat"
            archive_key = f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{archive_name}"
            s3.upload_file(local_file, S3_BUCKET, archive_key)
            log_to_file(f"🗄️ Archived new file to S3: {archive_key}")

            secret = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
            remote_iota_path = os.path.join(secret["remote_path"], filename).replace("\\", "/")
            archive_iota_path = os.path.join(secret["archive_path"], archive_name).replace("\\", "/")
            merged_path = f"/tmp/merged_{filename}"

            staging_key = f"{STAGING_PREFIX}iota.dat"
            staging_path = "/tmp/staging_iota.dat"
            staging_exists = False
            try:
                s3.download_file(S3_BUCKET, staging_key, staging_path)
                staging_exists = True
                log_to_file("📦 Staging file exists.")
            except Exception:
                log_to_file("ℹ️ No staging file found.")

            try:
                sftp, transport = connect_sftp(secret)

                existing_path = "/tmp/existing_iota.dat"
                iota_exists = False
                try:
                    sftp.get(remote_iota_path, existing_path)
                    iota_exists = True
                    log_to_file("📦 IOTA existing file found.")
                except Exception:
                    log_to_file("ℹ️ No existing IOTA file found.")

                # MERGE LOGIC
                if staging_exists and iota_exists:
                    send_sns("Merge Alert", "3-way Merge: Staging + IOTA + New")
                    temp_merge1 = "/tmp/temp_merge1.dat"
                    merge_files(staging_path, existing_path, temp_merge1)
                    merge_files(temp_merge1, local_file, merged_path)
                elif staging_exists:
                    send_sns("Merge Alert", "Merging: Staging + New")
                    merge_files(staging_path, local_file, merged_path)
                elif iota_exists:
                    send_sns("Merge Alert", "Merging: IOTA + New")
                    merge_files(existing_path, local_file, merged_path)
                else:
                    merged_path = local_file  # No merge

                final_archive = (
                    f"{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                    if merged_path != local_file else archive_name
                )

                # Upload to IOTA
                sftp.put(local_file, archive_iota_path)
                log_to_file(f"🗄️ Archived new file to IOTA: {archive_iota_path}")
                sftp.put(merged_path, remote_iota_path)
                log_to_file("📤 Uploaded merged/new file to IOTA.")

                sftp.put(
                    merged_path,
                    os.path.join(secret["archive_path"], final_archive).replace("\\", "/"))
                s3.upload_file(merged_path, S3_BUCKET,
                               f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{final_archive}")
                log_to_file("🗄️ Archived merged file to S3 and IOTA.")

                # Cleanup
                if staging_exists:
                    s3.delete_object(Bucket=S3_BUCKET, Key=staging_key)
                    log_to_file("🧹 Deleted staging file after successful 3-way merge.")

                sftp.close()
                transport.close()
                log_to_file("🔌 SFTP closed.")
                send_sns("✅ Transfer Success", "File successfully transferred to IOTA.")
                s3.delete_object(Bucket=S3_BUCKET, Key=key)
                log_to_file("🧹 Deleted original S3 landing file.")

            except Exception as e:
                log_to_file(f"⚠️ IOTA upload failed: {e}")
                send_sns("IOTA Down - Fallback to Staging", str(e))

                try:
                    if staging_exists:
                        send_sns("Staging Merge Begin", "IOTA down. Merging with staging file.")
                        merge_files(staging_path, local_file, merged_path)
                        final_archive = f"{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                        s3.upload_file(merged_path, S3_BUCKET,
                                       f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{final_archive}")
                        s3.upload_file(merged_path, S3_BUCKET, staging_key)
                        log_to_file("🛑 Merged file updated in staging.")
                        send_sns("Staging Merge Complete", "Updated staging file with merge.")
                    else:
                        s3.upload_file(local_file, S3_BUCKET, staging_key)
                        log_to_file("🛑 File moved to staging.")
                        send_sns("Staging Fallback", "Saved new file to staging.")
                    s3.delete_object(Bucket=S3_BUCKET, Key=key)
                    log_to_file("🧹 Deleted original S3 landing file after fallback.")
                except Exception as ex:
                    log_to_file(f"❌ Fallback failed: {ex}")
                    send_sns("Staging Fallback Failed", str(ex))

    except Exception as e:
        log_to_file(f"❌ Lambda failed: {e}")
        send_sns("Lambda Error", str(e))

    finally:
        try:
            log_key = f"{LOG_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%H%M%S')}_log.txt"
            boto3.client("s3").upload_file(log_file_path, S3_BUCKET, log_key)
        except Exception as e:
            send_sns("Log Upload Failed", str(e))
