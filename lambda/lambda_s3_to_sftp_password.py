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

# --- EMAIL SCENARIOS --- #
EMAIL_ALERTS = {
    "E-1": ("File Incoming", "File received from Inspire S3.\n\nFilename: {fname}"),
    "E-2": ("Invalid File", "Rejected file. Only 'iota.dat' is allowed.\n\nFilename: {fname}"),
    "E-3": ("File Archived", "The new file has been archived to S3.\n\nFilename: {fname}"),
    "E-4": ("IOTA Unreachable", "Unable to connect to Windows (IOTA) server. Fallback triggered.\n\nFile: {fname}"),
    "E-5": ("Merge Alert: Existing File Found on IOTA - Merge About to Start",
            "A merge operation will be performed between:\n- New File from S3: {fname}\n- Existing File on IOTA Server: {remote_path}\n\nMerge will begin shortly."),
    "E-6": ("Staging Merge Performed", "Staging file merge operation was triggered as an earlier file was not sent due to server unavailability.\n\nStaging File: {staging_key}\nNew File: {key}\nMerged File will be transferred to IOTA server if server is available.\nFile: {fname}"),
    "E-7": ("3-Way Merge Performed", "A 3-way merge between Staging, IOTA, and new file has been performed.\nStaging File: {staging_key}\nIOTA File: {remote_path}\nNew File: {key}\nMerged File transferred to IOTA server.\nFile: {fname}"),
    "E-8": ("File Transfer Complete", "File transfer completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8M": ("Merge Complete", "Merge completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8S": ("Staging Merge Complete", "Staging merge completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8T": ("3-Way Merge Complete", "3-way merge completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-9": ("Fallback: File moved to staging", "Unable to reach IOTA backend server. File moved to Staging folder:\n{s3_staging_path}\nFile: {fname}"),
    "E-10": ("Fallback: New file moved to staging", "Unable to reach IOTA backend server. New file moved to Staging folder:\n{s3_staging_path}\nFile: {fname}"),
    "E-11": ("Staging Merge Failure", "The merge operation from the staging folder failed due to an exception.\n\nError: {error}\nFile: {fname}\nStaging File: {staging_key}\nNew File: {key}"),
    "E-12": ("File Size Mismatch", "File size mismatch after transfer to IOTA. Manual check required.\nFile: {fname}"),
    "E-13": ("Invalid File Name Received", "File {fname} does not match expected file name \"iota.dat\".\nFile will not be processed.\nTimestamp: {now} JST"),
    "E-14": ("IOTA Archival Complete", "File also archived to IOTA archive folder.\n\nArchive Path: {archive_path}\nFile: {fname}"),
    "E-15": ("Unrecognized File Type in S3 Bucket", "A file was uploaded that does not match the required '.dat' extension.\n\nFile name: {fname}\n\nThis file has been ignored and not transferred to the IOTA server.\nPlease ensure only '.dat' files are moved to the staging folder."),
    "E-16": ("File Read Failure Detected For Existing iota.dat",
             "The existing file(iota.dat) in the remote IOTA server landing path is either corrupted or unreadable.\nMerge aborted. The new file will remain in Staging Folder in AWS S3.\nFile: {fname}\nRemote path: {remote_path}"),
    "E-17": ("File Archival Failure Alert", "The iota.dat file was successfully transferred/merged but failed during archival to one or both of the following paths:\n\n- AWS S3 Archive Path: {s3_archive_path}\n- IOTA Archive Path: {archive_path}\n\nError: {error}\nFile: {fname}")
}

def get_jst_now():
    return datetime.now(timezone.utc) + timedelta(hours=9)

def setup_logger(context=None):
    now = get_jst_now()
    req_id = getattr(context, 'aws_request_id', None)
    if req_id:
        path = f"/tmp/lambda-log-{now.strftime('%Y%m%d-%H%M%S')}-{req_id}.log"
    else:
        path = f"/tmp/lambda-log-{now.strftime('%Y%m%d-%H%M%S')}.log"
    open(path, 'a').close()
    return path

def log_to_file(msg):
    ts = get_jst_now().isoformat()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{ts} - {msg}\n")

def send_sns(code, params=None):
    params = params or {}
    template_keys = ["fname", "error", "s3_archive_path", "archive_path", "remote_path", "staging_key", "key", "now", "s3_staging_path"]
    for k in template_keys:
        params.setdefault(k, "-")
    subject, body = EMAIL_ALERTS.get(code, ("Unknown", "No message available."))
    try:
        subject_filled = subject.format(**params)
        body_filled = body.format(**params)
    except Exception:
        subject_filled = subject
        body_filled = body
    try:
        boto3.client("sns").publish(
            TopicArn=SNS_TOPIC,
            Subject=subject_filled,
            Message=body_filled
        )
        log_to_file(f"SNS Alert [{code}]: {subject_filled}")
    except Exception as e:
        log_to_file(f"Failed to send SNS [{code}]: {e}")

def sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()

def merge_files(paths, output):
    with open(output, "wb") as o:
        for p in paths:
            with open(p, "rb") as f:
                o.write(f.read())
    log_to_file(f"Files merged: {paths} -> {output}")
    log_to_file(f"SHA-256 of merged: {sha256(output)}")

def connect_sftp(secret):
    t = paramiko.Transport((secret["host"], int(secret["port"])))
    t.connect(username=secret["username"], password=secret["password"])
    return paramiko.SFTPClient.from_transport(t), t

def lambda_handler(event, context):
    global log_path
    log_path = setup_logger(context)  # Per-invocation unique log

    now = get_jst_now()
    s3 = boto3.client("s3")
    secrets = boto3.client("secretsmanager")

    try:
        for rec in event["Records"]:
            key = rec["s3"]["object"]["key"]
            fname = os.path.basename(key).strip()
            params = {"fname": fname, "key": key, "now": now.strftime('%Y-%m-%d %H:%M:%S')}

            # --- File extension and filename check (one block) ---
            if not fname.lower().endswith(".dat"):
                log_to_file(f"Skipped non-.dat file: {fname}")
                send_sns("E-15", params)
                continue
            if fname.lower() != "iota.dat":
                log_to_file(f"Skipped non-iota file: {fname}")
                send_sns("E-13", params)
                continue

            # --- Download file ---
            local_file = f"/tmp/{fname}"
            try:
                s3.download_file(S3_BUCKET, key, local_file)
            except Exception as ex:
                log_to_file(f"Download failed: {ex}")
                send_sns("E-17", {**params, "error": str(ex), "s3_archive_path": key, "archive_path": ""})
                continue
            log_to_file(f"Downloaded: {key}")
            log_to_file(f"SHA256: {sha256(local_file)}")
            log_to_file(f"Size: {os.path.getsize(local_file)} bytes")
            send_sns("E-1", params)

            s3_archive_key = f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%Y%m%d%H%M%S')}_iota.dat"
            try:
                s3.upload_file(local_file, S3_BUCKET, s3_archive_key)
                send_sns("E-3", {**params, "s3_archive_path": s3_archive_key})
                log_to_file("Archived to S3.")
            except Exception as ex:
                log_to_file(f"S3 archival failed: {ex}")
                send_sns("E-17", {**params, "error": str(ex), "s3_archive_path": s3_archive_key, "archive_path": ""})

            try:
                secret = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
            except Exception as ex:
                log_to_file(f"SecretManager fetch failed: {ex}")
                continue

            remote_path = os.path.join(secret["remote_path"], fname).replace("\\", "/")
            archive_path = os.path.join(secret["archive_path"], f"{now.strftime('%Y%m%d%H%M%S')}_iota.dat").replace("\\", "/")
            merged_out = f"/tmp/merged_iota.dat"

            staging_key = f"{STAGING_PREFIX}iota.dat"
            staging_file = "/tmp/staging_iota.dat"
            iota_file = "/tmp/existing_iota.dat"
            staging_exists, iota_exists = False, False

            try:
                s3.download_file(S3_BUCKET, staging_key, staging_file)
                staging_exists = True
                log_to_file("Staging file exists.")
            except Exception:
                log_to_file("No staging file found.")

            sftp = t = None
            try:
                sftp, t = connect_sftp(secret)
                log_to_file("IOTA server reachable.")
                try:
                    sftp.get(remote_path, iota_file)
                    iota_exists = True
                    log_to_file("IOTA file exists for merge.")
                except Exception as ex:
                    log_to_file(f"IOTA file not found: {ex}")
                    if "corrupt" in str(ex).lower() or "read" in str(ex).lower():
                        send_sns("E-16", {**params, "remote_path": remote_path})

            except Exception as ex:
                send_sns("E-4", params)
                log_to_file(f"SFTP connection failed: {ex}")
                sftp = t = None

            merge_flag = False
            merge_type = None  # "3way", "staging", "iota", or None
            try:
                if sftp:
                    if staging_exists and iota_exists:
                        send_sns("E-7", {**params, "remote_path": remote_path, "staging_key": staging_key})
                        try:
                            merge_files([staging_file, iota_file, local_file], merged_out)
                            merge_flag = True
                            merge_type = "3way"
                            s3.delete_object(Bucket=S3_BUCKET, Key=staging_key)
                            log_to_file("3-way merge (staging+IOTA+new) complete.")
                        except Exception as ex:
                            send_sns("E-7", {**params, "error": str(ex), "staging_key": staging_key})
                            log_to_file(f"3-way merge failed: {ex}")
                            continue

                    elif staging_exists:
                        send_sns("E-6", {**params, "staging_key": staging_key})
                        try:
                            merge_files([staging_file, local_file], merged_out)
                            merge_flag = True
                            merge_type = "staging"
                            s3.delete_object(Bucket=S3_BUCKET, Key=staging_key)
                            log_to_file("Staging+new merge complete.")
                        except Exception as ex:
                            send_sns("E-11", {**params, "error": str(ex), "staging_key": staging_key})
                            log_to_file(f"Staging merge failed: {ex}")
                            continue

                    elif iota_exists:
                        send_sns("E-5", {**params, "remote_path": remote_path})
                        try:
                            merge_files([iota_file, local_file], merged_out)
                            merge_flag = True
                            merge_type = "iota"
                            log_to_file("IOTA+new merge complete.")
                        except Exception as ex:
                            send_sns("E-7", {**params, "error": str(ex)})
                            log_to_file(f"IOTA merge failed: {ex}")
                            continue

                    try:
                        sftp.put(local_file, archive_path)
                        send_sns("E-14", {**params, "archive_path": archive_path})
                        log_to_file(f"Archived to IOTA: {archive_path}")
                    except Exception as ex:
                        send_sns("E-17", {**params, "error": str(ex), "archive_path": archive_path, "s3_archive_path": s3_archive_key})
                        log_to_file(f"IOTA archival failed: {ex}")

                    try:
                        sftp.put(merged_out if merge_flag else local_file, remote_path)
                        log_to_file("Uploaded to IOTA.")
                    except Exception as ex:
                        send_sns("E-17", {**params, "error": str(ex), "archive_path": remote_path, "s3_archive_path": s3_archive_key})
                        log_to_file(f"IOTA upload failed: {ex}")
                        continue

                    # --- Size Integrity Check and Success Notification ---
                    try:
                        r_size = sftp.stat(remote_path).st_size
                        l_size = os.path.getsize(merged_out if merge_flag else local_file)
                        log_to_file(f"Integrity check: remote={r_size}, local={l_size}")
                        if r_size != l_size:
                            send_sns("E-12", {**params})
                            log_to_file(f"Size mismatch: remote={r_size}, local={l_size}")
                        else:
                            if merge_type == "3way":
                                send_sns("E-8T", params)
                                log_to_file("3-way merge integrity check passed.")
                            elif merge_type == "staging":
                                send_sns("E-8S", params)
                                log_to_file("Staging merge integrity check passed.")
                            elif merge_type == "iota":
                                send_sns("E-8M", params)
                                log_to_file("IOTA merge integrity check passed.")
                            else:
                                send_sns("E-8", params)
                                log_to_file("File transfer integrity check passed: sizes match.")
                    except Exception as ex:
                        log_to_file(f"Size check error: {ex}")

                    # Archive merged (E-15) for any merge
                    if merge_flag:
                        merged_key = f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                        try:
                            s3.upload_file(merged_out, S3_BUCKET, merged_key)
                            sftp.put(merged_out, os.path.join(secret["archive_path"], f"{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat").replace("\\", "/"))
                            log_to_file(f"Staging merge archived as: {merged_key}")
                        except Exception as ex:
                            send_sns("E-17", {**params, "error": str(ex), "archive_path": archive_path, "s3_archive_path": merged_key})
                            log_to_file(f"Merged file archival failed: {ex}")

                    sftp.close(); t.close(); log_to_file("SFTP closed.")

                else:
                    # --- Fallback Logic (IOTA unreachable) ---
                    s3_staging_path = staging_key
                    try:
                        if staging_exists:
                            try:
                                merge_files([staging_file, local_file], merged_out)
                                s3.upload_file(merged_out, S3_BUCKET, staging_key)
                                # Archive merged file in S3
                                merged_key = f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                                s3.upload_file(merged_out, S3_BUCKET, merged_key)
                                log_to_file(f"Staging merge archived as: {merged_key}")
                                send_sns("E-6", {**params, "staging_key": staging_key})
                                send_sns("E-8S", params)
                                log_to_file("Updated staging with merged file.")
                            except Exception as ex:
                                send_sns("E-11", {**params, "error": str(ex), "staging_key": staging_key})
                                log_to_file(f"Staging merge fallback failed: {ex}")
                                continue
                        else:
                            s3.upload_file(local_file, S3_BUCKET, staging_key)
                            send_sns("E-10", {**params, "s3_staging_path": s3_staging_path})
                            log_to_file("Stored new file to staging.")
                    except Exception as ex:
                        log_to_file(f"Fallback staging failed: {ex}")
                        send_sns("E-17", {**params, "error": str(ex), "s3_archive_path": s3_staging_path, "archive_path": ""})

                s3.delete_object(Bucket=S3_BUCKET, Key=key)
                log_to_file("Cleaned up landing file.")

            except Exception as e:
                send_sns("E-13", {**params, "error": str(e)})
                log_to_file(f"Lambda failure: {e}")

    except Exception as e:
        send_sns("E-13", {"error": str(e)})
        log_to_file(f"Lambda global failure: {e}")

    finally:
        try:
            log_key = f"{LOG_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%H%M%S')}_log.txt"
            boto3.client("s3").upload_file(log_path, S3_BUCKET, log_key)
        except Exception as e:
            log_to_file(f"Log upload failed: {e}")
