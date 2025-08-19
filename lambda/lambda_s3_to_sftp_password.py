import os
import json
import boto3
import paramiko
import hashlib
from datetime import datetime, timezone, timedelta

# === ENVIRONMENT VARIABLES ===
S3_BUCKET = os.environ.get("S3_BUCKET")
SECRET_NAME = os.environ.get("SECRET_NAME")
SNS_TOPIC = os.environ.get("SNS_TOPIC")
LANDING_PREFIX = os.environ.get("LANDING_PREFIX", "aig-iota-landing-folder/")
STAGING_PREFIX = os.environ.get("STAGING_PREFIX", "staging/")
ARCHIVE_PREFIX = os.environ.get("ARCHIVE_PREFIX", "archived/")
LOG_PREFIX = os.environ.get("LOG_PREFIX", "logs/")

# === EMAIL NOTIFICATIONS / ALERT SCENARIOS ===
EMAIL_ALERTS = {
    "E-1": ("[Finatext] File Incoming: {fname}", "A new file has been received from Inspire S3 and detected in the landing folder.\n\nFilename: {fname}"),
    "E-2": ("[AIG] Invalid File Detected: {fname}", "A file has been rejected because only 'iota.dat' is allowed for processing.\nFilename: {fname}"),
    "E-3": ("[AIG] File Archived: {fname}", "The file has been successfully archived to S3.\nFilename: {fname}"),
    "E-4": ("[AIG] IOTA Server Unreachable – Fallback Initiated", "Unable to connect to the IOTA server. Fallback procedure has been triggered for file: {fname}"),
    "E-5": (
        "[AIG] Merge Alert: Existing File Found on IOTA - Merge About to Start",
        "A merge operation will be performed between:\n- New File from S3: {fname}\n- Existing File on IOTA Server: {remote_path}\n\nMerge will begin shortly."
    ),
    "E-6": (
        "[AIG] Staging Merge will be Performed",
        "Staging file merge operation was triggered as an earlier file was not sent due to server unavailability.\n\n"
        "Staging File: {staging_key}\nNew File: {key}\nMerged File will be transferred to IOTA server if server is available.\nFile: {fname}"
    ),
    "E-7": (
        "[AIG] Merge Failure: iota.dat",
        "The Merge operation failed during standard processing (non-staging flow).\n\n"
        "Details:\n- Source File (S3): s3://{S3_BUCKET}/{key}\n- Existing File (Remote): {remote_path}\nError: {error}\n\n"
        "Next Steps:\n- Please manually verify if the merge is required.\n- Consider retrieving logs from S3 (log folder: s3://{S3_BUCKET}/{log_key})"
    ),
    "E-8": ("[AIG] File Transfer Complete: {fname}", "The file has been successfully transferred to IOTA.\nFilename: {fname}"),
    "E-8M": ("[AIG] Merge Complete: {fname}", "Merge completed and transferred to IOTA.\nFilename: {fname}"),
    "E-8T": ("[AIG] 3-Way Merge Complete: {fname}", "3-way merge completed between new_file, S3_staging_file and IOTA_server_file. Successfully transferred to IOTA.\n\nFile: {fname}"),
    "E-8S": ("[AIG] Staging Merge Complete: {fname}", "Staging merge completed and transferred to IOTA.\n\nFile: {fname}"),
    "E-8SF": (
        "[AIG] Staging Merge Complete – Fallback",
        "Staging merge completed, but IOTA server was unavailable.\n"
        "Merged file remains in S3 staging until server is reachable.\nFile: {fname}"
    ),
    "E-9": (
        "[AIG] Fallback: File Moved to Staging",
        "Unable to reach IOTA backend server. File has been securely stored in the staging folder:\n{s3_staging_path}\nFile: {fname}"
    ),
    "E-10": (
        "[AIG] Fallback: New File Moved to Staging",
        "Unable to reach IOTA backend server. New file moved to Staging folder:\n{s3_staging_path}\nFile: {fname}"
    ),
    "E-11": (
        "[AIG] Staging Merge Failure: iota.dat",
        "The merge operation from the staging folder failed due to an exception.\n\n"
        "Details:\n- Staging File: s3://{S3_BUCKET}/{staging_key}\n- New File: s3://{S3_BUCKET}/{key}\n"
        "Merge operation could not be completed.\n"
        "The new file remains in its original incoming path: s3://{S3_BUCKET}/{key}\n\n"
        "Error: {error}\n\n"
        "Next Steps:\n- Please check the staging and incoming folders manually.\n- Review S3 logs in: s3://{S3_BUCKET}/{log_key}"
    ),
    "E-12": ("[AIG] File Size Mismatch: {fname}", "Manual review required due to size mismatch after transfer to IOTA.\nFilename: {fname}"),
    "E-13": (
        "[AIG] Invalid File Name Received: {fname}",
        "File {fname} does not match expected file name \"iota.dat\". File has been moved to staging and will not be processed.\nTimestamp: {now} JST"
    ),
    "E-14": ("[AIG] IOTA Archival Complete: {fname}", "File also archived to IOTA archive folder.\n\nArchive Path: {archive_path}\nFile: {fname}"),
    "E-15": (
        "[AIG] Unrecognized File Type: {fname}",
        "A file was uploaded that does not match the required '.dat' extension.\n\nFile name: {fname}\n\n"
        "This file has been moved to staging and ignored (not transferred to IOTA). Please ensure only '.dat' files are moved to the landing folder."
    ),
    "E-16": (
        "[AIG] File Read Failure Detected For Existing iota.dat",
        "The existing file(iota.dat) in the remote IOTA server landing path is either corrupted or unreadable.\n"
        "Merge aborted. The new file will remain in Staging Folder in AWS S3.\nFile: {fname}\nRemote path: {remote_path}"
    ),
    "E-17ARCH": (
        "[AIG] IOTA Archive Failure (Delivery Succeeded): {fname}",
        "The iota.dat file was delivered to IOTA Download folder, but archival to archive folder failed.\n\n"
        "Error: {error}\nDownload Path: {remote_path}\nArchive Path: {archive_path}\nFile: {fname}\n"
        "No recovery needed. File is available for downstream systems."
    ),
    "E-17S3": (
        "[AIG] S3 Archive Failure (Delivery Succeeded): {fname}",
        "The iotaAfterMerge.dat file was delivered to IOTA, but S3 archive failed.\n\n"
        "Error: {error}\nS3 Archive Path: {s3_archive_path}\nFile: {fname}\n"
        "No recovery needed. File is available for downstream systems."
    ),
    "E-LOG": (
        "[AIG] Log Upload Failure",
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
    template_keys = [
        "fname", "error", "s3_archive_path", "archive_path", "remote_path", "staging_key", "key",
        "now", "s3_staging_path", "log_key", "S3_BUCKET", "staging_file_path", "iota_file_path", "new_file_path"
    ]
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
    log_to_file("Lambda function execution started.")
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
                try:
                    s3.download_file(S3_BUCKET, key, local_file)
                    s3.upload_file(local_file, S3_BUCKET, staging_key)
                    log_to_file(f"Moved non-.dat file to staging: {staging_key}")
                except Exception as ex:
                    log_to_file(f"Failed to move non-.dat file to staging: {ex}")
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
                try:
                    s3.download_file(S3_BUCKET, key, local_file)
                    s3.upload_file(local_file, S3_BUCKET, staging_key)
                    log_to_file(f"Moved invalid .dat file to staging: {staging_key}")
                except Exception as ex:
                    log_to_file(f"Failed to move invalid .dat file to staging: {ex}")
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

            log_to_file(f"Downloaded: {key}")
            log_to_file(f"SHA256: {sha256(local_file)}")
            log_to_file(f"Size: {os.path.getsize(local_file)} bytes")
            send_sns("E-1", params)

            # --- Zero-byte file policy log ---
            if os.path.getsize(local_file) == 0:
                log_to_file("Warning: Zero-byte iota.dat file is being processed as normal per business policy.")

            # --- Archive file to S3 (with alert) ---
            s3_archive_key = f"{ARCHIVE_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%Y%m%d%H%M%S')}_iota.dat"
            try:
                s3.upload_file(local_file, S3_BUCKET, s3_archive_key)
                send_sns("E-3", {**params, "s3_archive_path": s3_archive_key})
                log_to_file("Archived to S3.")
            except Exception as ex:
                log_to_file(f"S3 archival failed: {ex}")
                send_sns("E-17S3", {**params, "error": str(ex), "s3_archive_path": s3_archive_key, "archive_path": ""})

            # --- Load SFTP secrets (host, user, etc.) ---
            try:
                secret = json.loads(secrets.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
            except Exception as ex:
                log_to_file(f"SecretManager fetch failed: {ex}")
                continue

            remote_path = os.path.join(secret["remote_path"], fname).replace("\\", "/")
            archive_path = os.path.join(secret["archive_path"], f"{now.strftime('%Y%m%d%H%M%S')}_iota.dat").replace("\\", "/")
            merged_out = f"/tmp/merged_iota.dat"

            # --- Check for existing files (staging in S3, iota.dat on remote) ---
            staging_key_iota = f"{STAGING_PREFIX}iota.dat"
            staging_file = "/tmp/staging_iota.dat"
            iota_file = "/tmp/existing_iota.dat"
            staging_exists, iota_exists = False, False

            try:
                s3.download_file(S3_BUCKET, staging_key_iota, staging_file)
                staging_exists = True
                log_to_file(f"Staging file exists: {staging_key_iota}")
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
                    log_to_file(f"IOTA file exists for merge: {remote_path}")
                except Exception as ex:
                    log_to_file("IOTA file not found")
                    if "corrupt" in str(ex).lower() or "read" in str(ex).lower():
                        send_sns("E-16", {**params, "remote_path": remote_path})

            except Exception as ex:
                send_sns("E-4", params)
                log_to_file(f"SFTP connection failed: {ex}")
                sftp = t = None
                iota_available = False

            merge_flag = False
            merge_type = None
            try:
                if sftp:
                    if staging_exists and iota_exists:
                        params.update({
                            "staging_file_path": staging_file,
                            "iota_file_path": iota_file,
                            "new_file_path": local_file
                        })
                        send_sns("E-5", {**params, "remote_path": remote_path, "staging_key": staging_key_iota})
                        log_to_file(f"Performing 3-way merge: staging_file={staging_file}, iota_file={iota_file}, new_file={local_file}")
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
                        log_to_file(f"Performing staging merge: staging_file={staging_file}, new_file={local_file}")
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
                        log_to_file(f"Performing IOTA merge: iota_file={iota_file}, new_file={local_file}")
                        try:
                            merge_files([iota_file, local_file], merged_out)
                            merge_flag = True
                            merge_type = "iota"
                            log_to_file("IOTA+new merge complete.")
                        except Exception as ex:
                            send_sns("E-7", {**params, "error": str(ex), "remote_path": remote_path})
                            log_to_file(f"IOTA merge failed: {ex}")
                            continue

                    # Archive file to IOTA archive folder (alert on fail)
                    try:
                        sftp.put(local_file, archive_path)
                        send_sns("E-14", {**params, "archive_path": archive_path})
                        log_to_file(f"Archived to IOTA: {archive_path}")
                    except Exception as ex:
                        send_sns("E-17ARCH", {**params, "error": str(ex), "archive_path": archive_path, "remote_path": remote_path})
                        log_to_file(f"IOTA archival failed: {ex}")

                    # Deliver merged (or original) to IOTA landing
                    try:
                        sftp.put(merged_out if merge_flag else local_file, remote_path)
                        log_to_file(f"Uploaded to IOTA: {remote_path}")
                    except Exception as ex:
                        send_sns("E-17ARCH", {**params, "error": str(ex), "archive_path": remote_path, "remote_path": remote_path})
                        log_to_file(f"IOTA upload failed: {ex}")
                        continue

                    # Post-upload: integrity check, final success notification
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

                    # Archive merged for any merge (with alert on fail)
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
                            if "s3" in str(ex).lower():
                                send_sns("E-17S3", {**params, "error": str(ex), "s3_archive_path": merged_key})
                            else:
                                archive_name = f"{now.strftime('%Y%m%d%H%M%S')}_iotaAfterMerge.dat"
                                iota_archive_path = os.path.join(secret['archive_path'], archive_name).replace("\\", "/")
                                send_sns("E-17ARCH", {**params, "error": str(ex), "archive_path": iota_archive_path, "remote_path": remote_path})
                            log_to_file(f"Merged file archival failed: {ex}")

                    sftp.close(); t.close(); log_to_file("SFTP closed.")

                else:
                    # Fallback: IOTA unreachable, handle staging/merging in S3 only
                    try:
                        if staging_exists:
                            try:
                                merge_files([staging_file, local_file], merged_out)
                                s3.upload_file(merged_out, S3_BUCKET, staging_key_iota)
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
        # Always upload Lambda log file to S3 at end of invocation
        log_to_file("Lambda function execution completed.")
        try:
            boto3.client("s3").upload_file(log_path, S3_BUCKET, log_key)
        except Exception as e:
            send_sns("E-LOG", {"error": str(e), "log_key": log_key})
            log_to_file(f"Log upload failed: {e}")
