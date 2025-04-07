#!/bin/bash

set -e

echo "🛠️ Building Lambda Layer..."

docker build -t lambda-layer-builder .

docker run --rm -v "$PWD":/out --entrypoint /bin/bash lambda-layer-builder \
  -c "cd /opt && zip -r /out/lambda-layer.zip python"

echo "✅ Layer built: lambda-layer.zip"

--chmod +x build.sh
./build.sh