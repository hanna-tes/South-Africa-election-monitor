# Ethiopian Election Monitor

Monitoring election-related narratives, hate speech, and coordination patterns for Ethiopia's 2026 election context.

This repository is the Code for Africa maintained version of the app. It is containerized for local development and is being prepared for deployment through the `cfa-iac-pulumi` infrastructure repository.

## Quick Start

The simplest way to run the app locally is Docker Compose. This starts:

- Django web app on port `8000`
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
git clone git@github.com:CodeForAfrica/ethiopian-election-monitor.git
cd ethiopian-election-monitor
git lfs pull
docker compose up -d --build
```

`git lfs install` is a one-time machine setup. The model itself lives in this
repository; Git LFS downloads the large binary while `git lfs pull` fetches it.
The single Docker command then builds the TEI sidecar and Django image, restores
the bundled legacy PostgreSQL dump into local Docker by default, runs
migrations, creates a local admin user, prepares the fast dashboard analytics,
and starts the web and worker containers.

No CFA AWS access is needed for local setup.

Default local admin login:

```text
URL: http://127.0.0.1:8000/admin/
Username: admin
Password: admin12345
```

To choose your own local admin credentials, keep it as one command:

```bash
DJANGO_SUPERUSER_USERNAME=myadmin DJANGO_SUPERUSER_PASSWORD='choose-a-local-password' docker compose up -d --build
```

To enable optional Groq-powered analysis locally, add the key to the same
command. Do not commit the key to the repository:

```bash
GROQ_API_KEY='your-groq-key' docker compose up -d --build
```

The local default Groq model is `llama-3.3-70b-versatile`. Set `GROQ_MODEL`
explicitly if the key has access to a different model.

To run an empty-database smoke test instead, opt out of the bundled restore on the same command:

```bash
SKIP_LEGACY_RESTORE=1 docker compose up -d --build
```

Open:

- App: http://127.0.0.1:8000/
- Networks page: http://127.0.0.1:8000/networks/
- Health check: http://127.0.0.1:8000/health/
- MinIO console: http://localhost:9001/

MinIO login:

```text
Username: minioadmin
Password: minioadmin
```

The local S3 bucket is created automatically:

```text
ethiopian-election-monitor-media
```

The first build is large because the TEI sidecar includes the AFRO-XLMR model.
The model is kept out of the Django web image. The first startup can also take
a few minutes because Docker restores the bundled database and precomputes
local analytics before the web app starts.

If another local project is already using ports `8000`, `9000`, or `9001`, use alternate host ports:

```bash
APP_PORT=8010 MINIO_API_PORT=9100 MINIO_CONSOLE_PORT=9101 docker compose up -d --build
```

Then open:

- App: http://127.0.0.1:8010/
- MinIO console: http://localhost:9101/

If that alternate app port is also busy, choose another high local port, for example:

```bash
APP_PORT=18021 MINIO_API_PORT=19120 MINIO_CONSOLE_PORT=19121 docker compose up -d --build
```

### Stop Everything

```bash
docker compose down
```

To remove local database and upload data too:

```bash
docker compose down -v
```

## Verify Local Setup

After starting the stack, run:

```bash
curl http://127.0.0.1:8000/health/
```

Expected response:

```json
{"status": "ok", "tei": "ok"}
```

The health check calls the TEI sidecar with one hate-speech example, one neutral
example, and one okay/non-hateful example. It returns HTTP `503` until all
three classifications match the expected result.

Check containers:

```bash
docker compose ps
```

You should see `web`, `worker`, `tei`, `postgres`, `minio`, and `redis` running.

On first startup, Docker may spend a few minutes in the `db-restore` and `app-setup` services before `web` starts. The setup step also refreshes the precomputed dashboard analytics, including the Trending Narratives clusters. If `GROQ_API_KEY` is supplied, narrative summaries can use Groq; otherwise the app falls back to local rule-based summaries. If the first health check fails, wait a few seconds and retry:

```bash
until curl -fsS http://127.0.0.1:8000/health/; do sleep 3; done
```

## Local Services

| Service | URL or Host | Notes |
| --- | --- | --- |
| Django app | http://127.0.0.1:8000/ | Main application |
| Django-Q worker | `worker` container | Runs queued background jobs |
| Health check | http://127.0.0.1:8000/health/ | Confirms Django and all three TEI checks are working |
| MinIO console | http://localhost:9001/ | Browser UI for uploaded files |
| MinIO S3 API | http://localhost:9000/ | S3-compatible API |
| PostgreSQL | `postgres:5432` | Internal Docker network only |
| Redis | `redis:6379` | Internal Docker network only |
| TEI model service | http://localhost:8085/ | Local AFRO-XLMR sidecar; authenticated predictions use the local TEI key |
| Bundled DB restore | `db-restore` service | Restores `docker/backups/ethiopia_election_db.dump` unless `SKIP_LEGACY_RESTORE=1` |
| Local app setup | `app-setup` service | Runs migrations and refreshes precomputed analytics, including narrative clusters, before `web` starts |

PostgreSQL and Redis are intentionally not exposed on host ports. The app connects to them inside the Docker network using `postgres:5432` and `redis:6379`, which is closer to how it will work in ECS/Fargate.

## Configuration

Local Docker Compose sets the required environment variables for you.

Important variables:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django secret key |
| `DEBUG` | Enables local debug mode when set to `1` |
| `ALLOWED_HOSTS` | Comma-separated list of allowed hosts |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins |
| `DATABASE_URL` | Optional full database URL |
| `DB_HOST` | PostgreSQL host, for Docker this is `postgres` |
| `DB_PORT` | PostgreSQL port, usually `5432` |
| `DB_NAME` | PostgreSQL database name |
| `DB_USER` | PostgreSQL user |
| `DB_PASSWORD` | PostgreSQL password |
| `DJANGO_SUPERUSER_USERNAME` | Local admin username created by Docker setup; defaults to `admin` |
| `DJANGO_SUPERUSER_EMAIL` | Local admin email created by Docker setup; defaults to `admin@example.local` |
| `DJANGO_SUPERUSER_PASSWORD` | Local admin password created by Docker setup; defaults to `admin12345` |
| `RESET_LOCAL_ADMIN_PASSWORD` | Set to `1` to reset an existing local admin user's password during setup |
| `CFA_VALKEY_URL` | Redis-compatible URL for CFA managed Valkey in deployment |
| `REDIS_URL` | Redis URL fallback; local Docker points it at `redis://redis:6379/1` |
| `AWS_STORAGE_BUCKET_NAME` | Enables S3-compatible media storage |
| `AWS_S3_ENDPOINT_URL` | S3 API endpoint, MinIO locally |
| `AWS_S3_CUSTOM_DOMAIN` | Public media URL host |
| `AWS_ACCESS_KEY_ID` | S3 or MinIO access key |
| `AWS_SECRET_ACCESS_KEY` | S3 or MinIO secret key |
| `GROQ_API_KEY` | Optional API key for LLM-powered features |
| `GROQ_MODEL` | Optional Groq model override |
| `TEI_API_KEY` | Local key shared by Django and the TEI sidecar; defaults to `local-test-key` |
| `TEI_IMAGE` | Optional pinned CPU TEI image override for the local machine architecture |
| `DATABASE_LOG_HANDLER_ENABLED` | Optional. Set to `1` only if ordinary Python logs should also be mirrored to the database |
| `APPLICATION_LOG_RETENTION_DAYS` | Number of days to retain `ApplicationLog` rows; defaults to `90` |
| `CLEAR_REDIS_CACHE_ON_STARTUP` | Deployment-only switch. Set to `1` to clear the Django cache when the web container starts; local Docker leaves it off by default |


If `AWS_STORAGE_BUCKET_NAME` is set, uploaded files go to S3-compatible storage. In local Docker, that means MinIO. In AWS, that should be a real S3 bucket provisioned through infrastructure.

If no database environment variables are set, Django falls back to SQLite. For normal development and deployment parity, prefer Docker Compose with PostgreSQL, Redis, and MinIO.

## Upload Storage

Uploads use Django's `default_storage`.

Local behavior:

- Files are saved to MinIO.
- The bucket is created automatically by the `minio-init` service.
- Media URLs use `http://localhost:9000/ethiopian-election-monitor-media/...`.

Production behavior:

- Files should be saved to AWS S3.
- The bucket, IAM permissions, secrets, and app environment variables should be managed through `cfa-iac-pulumi`.

## Redis and Background Jobs

Local Docker Compose runs Redis for Django cache and Django-Q background jobs.
It also starts a `worker` container with `python manage.py qcluster` so queued jobs are processed locally.

Production should use CFA managed Redis/Valkey and inject the Redis-compatible connection URL as:

```text
CFA_VALKEY_URL
```

The app also accepts `REDIS_URL` as a fallback for compatibility.

## Refresh Precomputed Analytics

Some dashboard pages intentionally avoid doing broad scans, clustering, and LLM work during normal page loads. After loading or uploading data, refresh the materialized analytics:

```bash
docker compose exec web python manage.py refresh_dashboard_analytics
```

This command:

- materializes per-post lexicon detections for the `/lexicons/` page
- prepares home-page trend, hashtag, and risk-actor summaries
- prepares narrative summaries for `/narratives/`
- prepares PEP/PIP mention analysis for `/peps/` and `/peps/analysis/`

For a faster local smoke test that skips narrative LLM/clustering work:

```bash
docker compose exec web python manage.py refresh_dashboard_analytics --skip-narratives
```

The real-time text scanner in `/lexicon-management/` still runs live when a user submits text. The refresh command only removes broad page-wide scans from ordinary browsing.

## Bundled Legacy Data

The normal Docker setup restores a bundled PostgreSQL dump into local Docker so the dashboards can be tested with realistic data immediately.

The dump lives at:

```text
docker/backups/ethiopia_election_db.dump
```

The bundled dump excludes local admin users, Django sessions, admin logs, and Django-Q task payload data. Docker setup creates a fresh local admin user after restore instead.

It is restored automatically by:

```bash
docker compose up -d --build
```

To skip bundled data and keep an empty local database:

```bash
SKIP_LEGACY_RESTORE=1 docker compose up -d --build
```

To force the bundled dump and analytics to rebuild after you have already started the stack:

```bash
FORCE_LEGACY_RESTORE=1 FORCE_ANALYTICS_REFRESH=1 docker compose up -d --build
```

To reset the local admin password too:

```bash
RESET_LOCAL_ADMIN_PASSWORD=1 DJANGO_SUPERUSER_PASSWORD='new-local-password' docker compose up -d --build
```

## LLM or Coding Agent Setup Instructions

If you are asking an LLM or coding agent to set this repo up locally, give it this prompt:

```text
Set up the Ethiopian Election Monitor repo locally.

Use Docker Compose as the primary setup path. Do not create a manual local database unless Docker Compose fails.

Steps:
1. Check that Git LFS is installed. If it is not, install it and run `git lfs install`.
2. Clone git@github.com:CodeForAfrica/ethiopian-election-monitor.git.
3. Change into the repo and run `git lfs pull` so the TEI model file is present.
4. Ask the tester what local Django admin username and password they want to use. If they do not care, use username admin and password admin12345.
5. Run one Docker command, including the chosen admin credentials, for example: DJANGO_SUPERUSER_USERNAME=admin DJANGO_SUPERUSER_PASSWORD='admin12345' docker compose up -d --build.
6. Wait while Docker builds the TEI sidecar and Django image, restores the bundled local PostgreSQL dump, runs migrations, creates the local admin user, refreshes precomputed analytics, and starts the web and worker containers. This can take a few minutes on first setup.
7. Wait until http://127.0.0.1:8000/health/ returns {"status": "ok", "tei": "ok"}. Retry every 3 seconds until it is ready.
8. Verify http://127.0.0.1:8000/networks/ returns HTTP 200.
9. Confirm Django is using PostgreSQL, not SQLite.
10. Verify http://127.0.0.1:8000/lexicons/, http://127.0.0.1:8000/lexicon-management/, and http://127.0.0.1:8000/peps/ return HTTP 200.
11. Confirm uploads use the S3-compatible Django storage backend backed by MinIO.
12. Confirm Django cache uses redis://redis:6379/1.
13. At the end, show the tester their local URLs and the admin username/password they chose.

The default setup does not need CFA AWS access. If the tester wants an empty local database instead of the bundled data, run SKIP_LEGACY_RESTORE=1 docker compose up -d --build.

Local URLs:
- App: http://127.0.0.1:8000/
- Networks: http://127.0.0.1:8000/networks/
- Health: http://127.0.0.1:8000/health/
- Admin: http://127.0.0.1:8000/admin/
- MinIO console: http://localhost:9001/

MinIO credentials:
- Username: minioadmin
- Password: minioadmin

If port 5432 is already in use on the host, do not change the app database host. The app should use postgres:5432 inside Docker. PostgreSQL does not need to be exposed to the host.
```

Useful verification command for an agent:

```bash
docker compose exec -T web python manage.py shell -c "from django.core.files.base import ContentFile; from django.core.files.storage import default_storage; from django.db import connection; path=default_storage.save('uploads/setup-smoke.txt', ContentFile(b'ok')); print(connection.settings_dict['ENGINE']); print(connection.settings_dict.get('HOST')); print(default_storage.__class__); print(default_storage.open(path, 'rb').read().decode()); print(default_storage.url(path))"
```

Expected signs of success:

```text
django.db.backends.postgresql
postgres
storages.backends.s3.S3Storage
ok
```

Redis verification:

```bash
docker compose exec -T web python manage.py shell -c "from django.conf import settings; from django.core.cache import cache; cache.set('setup-smoke', 'ok', 30); print(settings.REDIS_URL); print(cache.get('setup-smoke'))"
```

Expected:

```text
redis://redis:6379/1
ok
```

## Manual Setup Without Docker

Docker Compose is recommended. Use this only when Docker is unavailable.

Create a Python virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create a local `.env` file:

```bash
SECRET_KEY=local-dev-secret
DEBUG=1
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost,http://127.0.0.1
```

With only those values, Django will use SQLite and local filesystem media. For parity with deployment, run PostgreSQL and S3-compatible storage and set the database plus `AWS_*` variables listed above.

If you run background jobs or cache features without Docker, also run Redis and set:

```bash
export CFA_VALKEY_URL=redis://127.0.0.1:6379/1
```

Run migrations and start Django:

```bash
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

## Deployment Direction

This app is intended to deploy through Code for Africa infrastructure automation in `cfa-iac-pulumi`.

The production shape should be:

- Django app container on ECS/Fargate
- PostgreSQL via RDS
- Redis via CFA managed Valkey, injected as `CFA_VALKEY_URL`
- Uploads via S3
- Secrets and app configuration managed by infrastructure
- Health check at `/health/`, including three TEI inference checks
- Web and worker images built from this repo's `Dockerfile`
- TEI sidecar image built from `models/Dockerfile.tei` with the LFS-backed model

For an operator deploying the current ECS dev stack from a local checkout, use
the local AWS-backed deployment script after the infrastructure stack has
created the ECR repositories:

```bash
AWS_PROFILE=cfa-bootstrap ./scripts/deploy-locally.sh
```

It builds and pushes the web, worker, and TEI sidecar images, updates the ECS
task definition, applies scheduled-job overrides, and checks the public health
endpoint. This is separate from the normal local-only `docker compose` setup.

Keep local development aligned with that shape by using Docker Compose: app container, PostgreSQL, and S3-compatible object storage.
