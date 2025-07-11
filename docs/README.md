# Japan Travel Separation - AWS S3 to IOTA File Transfer

## Overview

This project automates the secure transfer and archival of daily data files (`iota.dat`) from an AWS S3 bucket to an on-premises IOTA (Windows) server via SFTP.  
It includes production-ready error handling, file integrity validation, SNS/email notifications, file merge and fallback logic, archival retention, and detailed logging.

---

## Features

- **AWS Lambda** (Python 3.11) for S3 event-based file processing
- **Paramiko** for SFTP transfer to IOTA server
- **SNS alerts** for all key events and errors
- **Robust merge logic** (handles IOTA/server downtime & file conflicts)
- **Automatic archival** and S3 lifecycle management
- **Watchtower Lambda** for scheduled file presence checks
- **End-to-end logging** (logs archived to S3)

---

## Architecture
+———————+
|    AWS S3 Bucket    | <— (landing: aig-iota-landing-folder/)
+———————+
|
(S3 Event trigger)
v
+———————+      +––––––––+
|    Lambda (Main)    |—–>| IOTA Windows   |
|  s3-to-iota-lambda  | SFTP |   Server       |
+———————+      +––––––––+
|
(SNS/Email alerts, logs, archive, staging)
|
+———————+
|    S3 Bucket        |
|   (archived/, logs/, staging/) |
+———————+
|
+———————+
| Watchtower Lambda   |
| (scheduled S3 file  |
|  presence checker)  |
+———————+
---

## Usage

- Place the `iota.dat` file in `aig-iota-landing-folder/` (S3).
- Lambda automatically triggers, validates, archives, transfers, and logs the process.
- SNS/email alerts sent for file arrival, transfer, merge, failure, etc.
- Logs and archived files are retained per S3 lifecycle rules.

---

## Deployment

### 1. **S3 Bucket**
- Ensure bucket (e.g., `aig-travel-seperation`) exists with folders:
  - `aig-iota-landing-folder/`
  - `archived/`
  - `staging/`
  - `logs/`

### 2. **IAM Roles & Policies**
- Lambda role with permissions:
  - S3 (Get, Put, Delete)
  - SNS:Publish
  - SecretsManager:GetSecretValue
  - CloudWatch (if needed)

### 3. **Secrets Manager**
- Store SFTP credentials (JSON: host, port, username, password, remote_path, archive_path)
- Secret name: e.g., `aig-sftp-creds`

### 4. **SNS Topic**
- Create topic for alerts (e.g., `arn:aws:sns:ap-northeast-1:xxx:topic/apne1-prod-sftp-alerts`)
- Subscribe team email(s)

### 5. **Lambda Function**
- Python 3.11 runtime, with packaged dependencies (Paramiko, Watchtower)
- Attach Lambda Layer if needed
- Set environment variables as below

### 6. **EventBridge Scheduler (Watchtower)**
- Scheduled rule (e.g., daily 07:10 JST) to trigger Watchtower Lambda for file presence check

### 7. **S3 Lifecycle Policy**
- Configure `archived/` and `logs/` folders for automatic expiry (e.g., delete after 30 days)

---

## Environment Variables

| Key            | Value (Example)                                          |
|----------------|---------------------------------------------------------|
| ARCHIVE_PREFIX | archived/                                               |
| EXPECTED_FILE  | iota.dat                                                |
| LANDING_PREFIX | aig-iota-landing-folder/                                |
| LOG_PREFIX     | logs/                                                   |
| MAX_RETRIES    | 3                                                       |
| MERGE_ENCODING | utf-8                                                   |
| S3_BUCKET      | aig-travel-seperation                                   |
| SECRET_NAME    | aig-sftp-creds                                          |
| SNS_TOPIC      | arn:aws:sns:ap-northeast-1:xxx:topic/apne1-prod-sftp-alerts |
| STAGING_PREFIX | staging/                                                |

---

## Rollback / Recovery

> **For detailed rollback manual, see [rollback_manual/README.md](rollback_manual/README.md)**

- To disconnect file transfer: Remove Lambda trigger from S3 and/or disable Lambda.
- To roll back code/config: Revert to previous Lambda version via GitHub or AWS console.
- Remove EventBridge rule to stop Watchtower checks if required.

---

## Repository Structure
/
├── lambda/                  # Main Lambda function
├── watchtower_lambda/       # Watchtower Lambda (file presence checker)
├── lambda_layer/            # Dockerfiles/requirements for Lambda Layer (Paramiko)
├── cloudformation/          # Infra-as-code (IAM, SNS, S3, etc.)
├── docs/                    # HLD, LLD, solution architecture, diagrams
├── rollback_manual/         # Rollback and recovery procedures
---

## Support / Contacts

- **AIG Japan Project Team** (see docs/contacts.md)
- For infra/incident support, contact AWS admin as per project SOP

---

## License

> Internal use only – AIG Japan | Confidential
