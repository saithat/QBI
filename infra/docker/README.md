# Local Docker runtime

The local stack is defined by the root `Dockerfile` and `compose.yaml`. It runs PostgreSQL, a
source-built MinIO service, vLLM, and the API/viewer. `minio/` pins the security-fixed upstream
source release and runs as a non-root user. Compose is a development runtime, not a durable
production design.
