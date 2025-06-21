# S3-to-IOTA Lambda Solution

A production-grade AWS Lambda for secure, auditable file transfer from AWS S3 to an IOTA (Windows) SFTP backend, featuring robust error handling, zero-downtime staging/merge logic, and fine-grained alerting.

---

## 🚀 Overview

This Lambda function automates the transfer of files from an S3 bucket to a Windows (IOTA) SFTP server, meeting enterprise-grade compliance and reliability requirements. It is **battle-tested for:**
- SFTP downtime handling (auto-staging in S3)
- Multi-way merge (3-way, normal, staging)
- Filename and extension enforcement
- S3 archiving with 1-month lifecycle support
- 18+ detailed SNS/email alert scenarios
- Per-invocation S3 logging

---

## ✨ Features

- **Strict file validation** (`iota.dat` only, zero-byte allowed)
- **Staging fallback:** If IOTA is down, file is moved to `/staging/` in S3
- **Merge engine:** Handles 3-way, normal, and staging merges (binary-safe)
- **S3 archival:** All incoming and merged files are archived to `/archived/` (supports S3 lifecycle)
- **Detailed alerts:** Covers file arrival, transfer, merge, staging, failures, and zero-byte edge cases
- **UTF-8 safe**, supports Japanese filenames/content
- **Self-contained logging:** All steps logged to `/logs/` in S3; log upload is itself monitored
- **Environment-driven:** No code changes needed for re-deployment or config tweaks

---

## 🏗️ Architecture

```plaintext
[S3: landing folder]
      |
      | (Lambda event)
      v
[AWS Lambda: s3-to-iota]
      |
  +-------------------+
  |   Validation      |
  |   S3 Archive      |
  |   Merge Logic     |
  |   Staging Logic   |
  |   SNS Alerts      |
  +-------------------+
      |
      v
[SFTP: IOTA server] <--- (fallback: S3 /staging/)

Landing: Files dropped to /aig-iota-landing-folder/ trigger the Lambda.
Archival: Every processed file is saved under /archived/YYYY-MM-DD/.
Logs: All Lambda runs create /logs/YYYY-MM-DD/ files.
Fallback: If SFTP fails, file is staged under /staging/.
Zero-byte: Handled per business policy (allowed & archived, warning in logs).

