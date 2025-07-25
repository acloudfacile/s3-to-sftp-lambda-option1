import os
import paramiko

def lambda_handler(event, context):
    host = os.environ["IOTA_HOST"]
    port = 22
    username = os.environ["IOTA_USER"]
    password = os.environ["IOTA_PASS"]

    try:
        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        print("✅ SFTP connection successful!")
        sftp.close()
        transport.close()
        return {"status": "success"}
    except Exception as e:
        print(f"❌ SFTP connection failed: {e}")
        return {"status": "failed", "error": str(e)}
