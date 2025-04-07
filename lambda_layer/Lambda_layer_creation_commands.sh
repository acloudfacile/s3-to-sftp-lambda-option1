# Create clean folder
mkdir -p ~/lambda-python311-layer
cd ~/lambda-python311-layer

# Create Dockerfile
cat <<EOF > Dockerfile
FROM public.ecr.aws/lambda/python:3.11

# Install zip and required packages
RUN yum install -y zip && \
    pip3 install paramiko watchtower -t /opt/python

CMD ["bash"]
EOF

# Build Docker image
docker build -t lambda-layer-builder-311 .

# Run container and zip the /opt/python directory
docker run --rm -v "$PWD":/out --entrypoint /bin/bash lambda-layer-builder-311 \
  -c "cd /opt && zip -r /out/lambda-layer-python311.zip python"