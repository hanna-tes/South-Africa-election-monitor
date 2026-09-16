# South Africa Election Monitor

Monitoring election-related narratives, hate speech, and coordination patterns for South Africa's election context.

## Quick Start

The simplest way to run the app locally is Docker Compose. This starts:

- Django web app on port `8010`
- Django-Q worker for background jobs
- PostgreSQL database inside Docker
- MinIO, a local S3-compatible object store, for uploads
- Redis inside Docker for cache and Django-Q background jobs
- a one-time local setup job that restores the bundled legacy PostgreSQL data and refreshes precomputed analytics

### Requirements

Install these first:

- Docker Desktop
- Git
- Git LFS

### Run Everything

```bash
git lfs install
git clone git@github.com:hanna-tes/South-Africa-election-monitor.git
cd south-africa-election-monitor
git lfs pull
docker compose up -d --build
