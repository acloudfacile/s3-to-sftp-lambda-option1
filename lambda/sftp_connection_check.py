import os
import boto3
import paramiko
import json

def get_secret(secret_name, region="ap-northeast-1"):
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region)
    get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    return json.loads(get_secret_value_response["SecretString"])

def lambda_handler(event, context):
    secret_name = os.environ.get("SECRET_NAME", "iota-sftp-creds")
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    creds = get_secret(secret_name, region)
    
    host = creds["host"]
    username = creds["username"]
    password = creds["password"]
    port = 22

    try:
        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        print("✅ SFTP connection successful")
        sftp.close()
        transport.close()
        return {"status": "success", "msg": "SFTP connection successful"}
    except Exception as e:
        print(f"❌ SFTP connection failed: {e}")
        return {"status": "failed", "error": str(e)}
