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
    "E-0": (
        "Zero-byte File Detected",
        "A zero-byte iota.dat file was detected and moved to staging.\n\n"
        "File: {fname}\n"
        "No transfer or merge performed. Please investigate why an empty file was uploaded."
    ),
    "E-1": ("File Incoming", "File received from Inspire S3.\n\nFilename: {fname}"),
    "E-2": ("Invalid File", "Rejected file. Only 'iota.dat' is allowed.\n\nFilename: {fname}"),
    "E-3": ("File Archived", "The new file has been archived to S3.\n\nFilename: {fname}"),
    "E-4": ("IOTA Unreachable", "Unable to connect to Windows (IOTA) server. Fallback triggered.\n\nFile: {fname}"),
    "E-5": ("Merge Alert: Existing File Found on IOTA - Merge About to Start",
            "A merge operation will be performed between:\n- New File from S3: {fname}\n- Existing File on IOTA Server: {remote_path}\n\nMerge will begin shortly."),
    "E-6": ("Staging Merge Performed", "Staging file merge operation was triggered as an earlier file was not sent due to server unavailability.\n\nStaging File: {staging_key}\nNew File: {key}\nMerged File will be transferred to IOTA server if server is available.\nFile: {fname}"),
    "E-7": (
        "Merge Failure for File 'iota.dat'",
        "Merge operation failed during standard processing (non-staging flow).\n\n"
        "Details:\n- Source File (S3): s3://{S3_BUCKET}/{key}\n- Existing File (Remote): {remote_path}\nError: {error}\n\n"
        "Next Steps:\n- Please manually verify if the merge is required.\n- Consider retrieving logs from S3 (log folder: s3://{S3_BUCKET}/{log_key})"
    ),
    "E-8": ("File Transfer Complete", "File transfer completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8M": ("Merge Complete", "Merge completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8T": ("3-Way Merge Complete", "3-way merge completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8S": ("Staging Merge Complete", "Staging merge completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8SF": ("Staging Merge Complete – Fallback",
              "Staging merge completed, but IOTA server was unavailable.\n"
              "Merged file remains in S3 staging until server is reachable.\nFile: {fname}"),
    "E-9": ("Fallback: File moved to staging", "Unable to reach IOTA backend server. File moved to Staging folder:\n{s3_staging_path}\nFile: {fname}"),
    "E-10": ("Fallback: New file moved to staging", "Unable to reach IOTA backend server. New file moved to Staging folder:\n{s3_staging_path}\nFile: {fname}"),
    "E-11": (
        "Staging Merge Failure for File 'iota.dat'",
        "The merge operation from the staging folder failed due to an exception.\n\n"
        "Details:\n- Staging File: s3://{S3_BUCKET}/{staging_key}\n- New File: s3://{S3_BUCKET}/{key}\n"
        "Merge operation could not be completed.\n"
        "The new file remains in its original incoming path: s3://{S3_BUCKET}/{key}\n\n"
        "Error: {error}\n\n"
        "Next Steps:\n- Please check the staging and incoming folders manually.\n- Review S3 logs in: s3://{S3_BUCKET}/{log_key}"
    ),
    "E-12": ("File Size Mismatch", "File size mismatch after transfer to IOTA. Manual check required.\nFile: {fname}"),
    "E-13": ("Invalid File Name Received", "File {fname} does not match expected file name \"iota.dat\". File has been moved to staging and will not be processed.\nTimestamp: {now} JST"),
    "E-14": ("IOTA Archival Complete", "File also archived to IOTA archive folder.\n\nArchive Path: {archive_path}\nFile: {fname}"),
    "E-15": ("Unrecognized File Type in S3 Bucket", "A file was uploaded that does not match the required '.dat' extension.\n\nFile name: {fname}\n\nThis file has been moved to staging and ignored (not transferred to IOTA). Please ensure only '.dat' files are moved to the landing folder."),
    "E-16": ("File Read Failure Detected For Existing iota.dat",
             "The existing file(iota.dat) in the remote IOTA server landing path is either corrupted or unreadable.\nMerge aborted. The new file will remain in Staging Folder in AWS S3.\nFile: {fname}\nRemote path: {remote_path}"),
    "E-17ARCH": (
        "IOTA Archive Failure – Main delivery succeeded",
        "The iota.dat file was delivered to IOTA Download folder, but archival to archive folder failed.\n\n"
        "Error: {error}\n"
        "Download Path: {remote_path}\n"
        "Archive Path: {archive_path}\n"
        "File: {fname}\n"
        "No recovery needed. File is available for downstream systems."
    ),
    "E-17S3": (
        "S3 Archive Failure – Main delivery succeeded",
        "The iotaAfterMerge.dat file was delivered to IOTA, but S3 archive failed.\n\n"
        "Error: {error}\n"
        "S3 Archive Path: {s3_archive_path}\n"
        "File: {fname}\n"
        "No recovery needed. File is available for downstream systems."
    ),
    "E-LOG": (
        "Log Upload Failure",
        "The log file failed to upload to S3.\n\nError: {error}\nLog Path: {log_key}\n"
    ),
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
    template_keys = ["fname", "error", "s3_archive_path", "archive_path", "remote_path", "staging_key", "key", "now", "s3_staging_path", "log_key", "S3_BUCKET"]
    for k in template_keys:
        params.setdefault(k, "-")
    params["S3_BUCKET"] = S3_BUCKET
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
    log_path = setup_logger(context)
    now = get_jst_now()
    s3 = boto3.client("s3")
    secrets = boto3.client("secretsmanager")
    log_key = f"{LOG_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%H%M%S')}_log.txt"

    try:
        for rec in event["Records"]:
            key = rec["s3"]["object"]["key"]
            fname = os.path.basename(key).strip()
            params = {
                "fname": fname, "key": key, "now": now.strftime('%Y-%m-%d %H:%M:%S'),
                "log_key": log_key, "S3_BUCKET": S3_BUCKET
            }
            local_file = f"/tmp/{fname}"
            staging_key = f"{STAGING_PREFIX}{fname}"

            # --- File extension check (E-15) ---
            if not fname.lower().endswith(".dat"):
                log_to_file(f"Rejected non-.dat file: {fname}")
                # Move to staging
                try:
                    s3.download_file(S3_BUCKET, key, local_file)
                    s3.upload_file(local_file, S3_BUCKET, staging_key)
                    log_to_file(f"Moved non-.dat file to staging: {staging_key}")
                except Exception as ex:
                    log_to_file(f"Failed to move non-.dat file to staging: {ex}")
                # Delete from landing
                try:
                    s3.delete_object(Bucket=S3_BUCKET, Key=key)
                    log_to_file(f"Deleted {fname} from landing after moving to staging.")
                except Exception as ex:
                    log_to_file(f"Failed to delete non-.dat file from landing: {ex}")
                send_sns("E-15", params)
                continue

            # --- Strict filename check (E-13) ---
            if fname.lower() != "iota.dat":
                log_to_file(f"Rejected file with invalid name: {fname}")
                # Move to staging
                try:
                    s3.download_file(S3_BUCKET, key, local_file)
                    s3.upload_file(local_file, S3_BUCKET, staging_key)
                    log_to_file(f"Moved invalid .dat file to staging: {staging_key}")
                except Exception as ex:
                    log_to_file(f"Failed to move invalid .dat file to staging: {ex}")
                # Delete from landing
                try:
                    s3.delete_object(Bucket=S3_BUCKET, Key=key)
                    log_to_file(f"Deleted {fname} from landing after moving to staging.")
                except Exception as ex:
                    log_to_file(f"Failed to delete invalid .dat file from landing: {ex}")
                send_sns("E-13", params)
                continue

            # --- Download file ---
            try:
                s3.download_file(S3_BUCKET, key, local_file)
            except Exception as ex:
                log_to_file(f"Download failed: {ex}")
                send_sns("E-17S3", {**params, "error": str(ex), "s3_archive_path": key, "archive_path": ""})
                continue

            # --- Zero-byte check (NEW ENHANCEMENT) ---
            if os.path.getsize(local_file) == 0:
                try:
                    s3.upload_file(local_file, S3_BUCKET, staging_key)
                    log_to_file("Zero-byte file detected. Moved to staging.")
                    s3.delete_object(Bucket=S3_BUCKET, Key=key)
                except Exception as ex:
                    log_to_file(f"Zero-byte move to staging failed: {ex}")
                send_sns("E-0", params)
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
                send_sns("E-17S3", {**params, "error": str(ex), "s3_archive_path": s3_archive_key, "archive_path": ""})

            try:
                secret = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
            except Exception as ex:
                log_to_file(f"SecretManager fetch failed: {ex}")
                continue

            remote_path = os.path.join(secret["remote_path"], fname).replace("\\", "/")
            archive_path = os.path.join(secret["archive_path"], f"{now.strftime('%Y%m%d%H%M%S')}_iota.dat").replace("\\", "/")
            merged_out = f"/tmp/merged_iota.dat"

            staging_key_iota = f"{STAGING_PREFIX}iota.dat"
            staging_file = "/tmp/staging_iota.dat"
            iota_file = "/tmp/existing_iota.dat"
            staging_exists, iota_exists = False, False

            try:
                s3.download_file(S3_BUCKET, staging_key_iota, staging_file)
                staging_exists = True
                log_to_file("Staging file exists.")
            except Exception:
                log_to_file("No staging file found.")

            sftp = t = None
            try:
                sftp, t = connect_sftp(secret)
                log_to_file("IOTA server reachable.")
                iota_available = True
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
                iota_available = False

            merge_flag = False
            merge_type = None  # "3way", "staging", "iota", or None
            try:
                if sftp:
                    if staging_exists and iota_exists:
                        send_sns("E-5", {**params, "remote_path": remote_path, "staging_key": staging_key_iota})
                        try:
                            merge_files([staging_file, iota_file, local_file], merged_out)
                            merge_flag = True
                            merge_type = "3way"
                            s3.delete_object(Bucket=S3_BUCKET, Key=staging_key_iota)
                            log_to_file("3-way merge (staging+IOTA+new) complete.")
                        except Exception as ex:
                            send_sns("E-11", {**params, "error": str(ex), "staging_key": staging_key_iota})
                            log_to_file(f"3-way merge failed: {ex}")
                            continue

                    elif staging_exists:
                        send_sns("E-6", {**params, "staging_key": staging_key_iota})
                        try:
                            merge_files([staging_file, local_file], merged_out)
                            merge_flag = True
                            merge_type = "staging"
                            s3.delete_object(Bucket=S3_BUCKET, Key=staging_key_iota)
                            log_to_file("Staging+new merge complete.")
                        except Exception as ex:
                            send_sns("E-11", {**params, "error": str(ex), "staging_key": staging_key_iota})
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
                            send_sns("E-7", {**params, "error": str(ex), "remote_path": remote_path})
                            log_to_file(f"IOTA merge failed: {ex}")
                            continue

                    try:
                        sftp.put(local_file, archive_path)
                        send_sns("E-14", {**params, "archive_path": archive_path})
                        log_to_file(f"Archived to IOTA: {archive_path}")
                    except Exception as ex:
                        send_sns("E-17ARCH", {**params, "error": str(ex), "archive_path": archive_path, "remote_path": remote_path})
                        log_to_file(f"IOTA archival failed: {ex}")

                    try:
                        sftp.put(merged_out if merge_flag else local_file, remote_path)
                        log_to_file("Uploaded to IOTA.")
                    except Exception as ex:
                        send_sns("E-17ARCH", {**params, "error": str(ex), "archive_path": remote_path, "remote_path": remote_path})
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

                    # Archive merged for any merge
                    if merge_flag:
                        merged_key = f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                        try:
                            s3.upload_file(merged_out, S3_BUCKET, merged_key)
                            archive_name = f"{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                            iota_archive_path = os.path.join(secret['archive_path'], archive_name).replace("\\", "/")
                            sftp.put(merged_out, iota_archive_path)
                            log_to_file(f"Staging merge archived as: {merged_key}")
                            log_to_file(f"Merged file archived to IOTA as: {iota_archive_path}")
                        except Exception as ex:
                            # Try to distinguish S3 archive vs IOTA archive
                            if "s3" in str(ex).lower():
                                send_sns("E-17S3", {**params, "error": str(ex), "s3_archive_path": merged_key})
                            else:
                                archive_name = f"{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                                iota_archive_path = os.path.join(secret['archive_path'], archive_name).replace("\\", "/")
                                send_sns("E-17ARCH", {**params, "error": str(ex), "archive_path": iota_archive_path, "remote_path": remote_path})
                            log_to_file(f"Merged file archival failed: {ex}")

                    sftp.close(); t.close(); log_to_file("SFTP closed.")

                else:
                    # --- Fallback Logic (IOTA unreachable) ---
                    try:
                        if staging_exists:
                            try:
                                merge_files([staging_file, local_file], merged_out)
                                s3.upload_file(merged_out, S3_BUCKET, staging_key_iota)
                                # Archive merged file in S3
                                merged_key = f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                                s3.upload_file(merged_out, S3_BUCKET, merged_key)
                                log_to_file(f"Staging merge archived as: {merged_key}")
                                send_sns("E-6", {**params, "staging_key": staging_key_iota})
                                send_sns("E-8SF", params)
                                log_to_file("Updated staging with merged file.")
                            except Exception as ex:
                                send_sns("E-11", {**params, "error": str(ex), "staging_key": staging_key_iota})
                                log_to_file(f"Staging merge fallback failed: {ex}")
                                continue
                        else:
                            s3.upload_file(local_file, S3_BUCKET, staging_key_iota)
                            send_sns("E-10", {**params, "s3_staging_path": staging_key_iota})
                            log_to_file("Stored new file to staging.")
                    except Exception as ex:
                        log_to_file(f"Fallback staging failed: {ex}")
                        send_sns("E-17S3", {**params, "error": str(ex), "s3_archive_path": staging_key_iota, "archive_path": ""})

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
            boto3.client("s3").upload_file(log_path, S3_BUCKET, log_key)
        except Exception as e:
            send_sns("E-LOG", {"error": str(e), "log_key": log_key})
            log_to_file(f"Log upload failed: {e}")
