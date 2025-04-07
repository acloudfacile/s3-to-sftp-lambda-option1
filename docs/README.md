# 🚀 S3 to SFTP Transfer using AWS Lambda (Option 1: Password-Based)

This project enables **automated transfer of `.dat` files from an S3 bucket to a remote Windows SFTP server**, using AWS Lambda (Python 3.11) with **Paramiko** and **Watchtower**. It also includes robust error handling, weekend alerts, and full CloudWatch logging.

---

## ✅ Features

- 🔐 Secure SFTP transfer using credentials from AWS Secrets Manager
- 📤 Uploads file to both `/incoming` and `/archive` folders on the SFTP server
- 🧊 Archives the file in S3 under date-wise folders (`archived/YYYY-MM-DD/`)
- 📅 Triggers only on `.dat` files
- 📛 Weekend uploads trigger a separate SNS alert
- 📦 ZIP Lambda Layer built with Docker (Paramiko + Watchtower)
- 📊 All logs pushed to a **single CloudWatch Log Group** per run (`s3-windows-iota`)
- 📬 SNS alerts for success and failure
- 🧽 Lifecycle policy (optional) to clean old S3 archives

---

## 🧾 Folder Structure
s3-to-sftp-lambda-option1/
│
├── lambda/
│   ├── lambda_function.py         # Final working Lambda function
│   └── requirements.txt           # Optional: for reference or testing
│
├── lambda_layer/
│   ├── Dockerfile                 # Dockerfile to build Paramiko+Watchtower layer
│   └── build.sh                   # Shell script to generate zip from Docker
│
├── iam/
│   └── iam-policy.json            # IAM policy required for Lambda execution
│
├── docs/
│   └── README.md                  # You’re reading it 😄
│
└── utils/
└── test_event.json            # Sample S3 trigger event for testing

---

## ⚙️ Environment Variables (Lambda)

| Variable       | Description                                |
|----------------|--------------------------------------------|
| `S3_BUCKET`     | Your input S3 bucket name                  |
| `S3_PREFIX`     | S3 folder prefix to monitor (`incoming/`) |
| `SECRET_NAME`   | Secrets Manager name with SFTP creds      |
| `SNS_TOPIC`     | SNS topic ARN for alerts                  |
| `WEEKEND_ALERT` | `true` or `false` to enable weekend alert |

---

## 🔑 Example Secret Format (AWS Secrets Manager)

```json
{
  "host": "13.113.xxx.xxx",
  "port": 22,
  "username": "windows-user",
  "password": "supersecret123",
  "remote_path": "/incoming",
  "archive_path": "/archive"
}

📦 Lambda Layer (Build via Docker)

Inside lambda_layer/:
# Build and package layer
./build.sh

✅ IAM Permissions

See cloudformation/iam-policy.json. It covers:
	•	SecretsManager
	•	S3 (Get, Put, Delete)
	•	SNS (Publish)
	•	CloudWatch Logs

🧹 Optional Enhancements
	•	Auto-delete S3 archives older than 30 days (via S3 lifecycle rule)

🙌 Credits

Crafted with ❤️ by Harsha Reddy Gogireddy — powered by AWS, Paramiko & Docker.