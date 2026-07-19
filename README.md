# Salesforce Service

**Salesforce Integration Service** — Extracts data from Salesforce CRM using the Bulk API 2.0 and feeds it into the Glynac data pipeline.

## Overview

This service acts as the dedicated extraction engine for Salesforce, mirroring the role that `master-service` plays for the wealth platform API. It uses the **Salesforce Bulk API 2.0** (Query Jobs) for large-scale reads — processing up to 100 million records per job with asynchronous polling and paginated CSV results.

## Architecture

```
[core-service]         <- Orchestrator (creates/tracks/monitors scan jobs)
         |
         | HTTP + HMAC (internal)
         v
[salesforce-service]   <- This service (Salesforce extractor)
         |
         | OAuth 2.0 (Connected App)
         v
[Salesforce Bulk API 2.0]            <- External data source
         |
         | (results)
         v
[MinIO]                              <- Storage
```

## Technology Stack

| Layer | Technology |
|---|---|
| Service framework | Flask-RESTX |
| Salesforce client | `simple-salesforce` + custom Bulk API 2.0 wrapper |
| Auth to Salesforce | OAuth 2.0 — JWT Bearer flow (Connected App) |
| Data serialization | CSV → Parquet (via pandas/pyarrow) |
| Object storage | MinIO (S3-compatible) |
| Secrets | HashiCorp Vault via Nomad template |
| Auth (internal) | HMAC (dual-key: core-key + engineer-key) |

## Supported Salesforce Objects

- Contact
- Account
- Opportunity
- Activity (Task + Event)
- Lead
- User
- CampaignMember

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/key/verify` | Verify HMAC key permissions |
| POST | `/api/scan/start` | Start a Salesforce extraction scan |
| GET | `/api/scan/list` | List all scans with filters |
| GET | `/api/scan/{id}/status` | Get scan status and progress |
| POST | `/api/scan/{id}/cancel` | Cancel an in-progress scan |
| POST | `/api/scan/{id}/resume` | Resume a failed scan |
| DELETE | `/api/scan/{id}/remove` | Remove a scan record |
| GET | `/api/scan/statistics` | Aggregate scan stats |
| POST | `/api/maintenance/cleanup` | Purge old scan records |
| GET | `/api/objects` | List supported Salesforce objects |
| GET | `/api/batch/info` | Salesforce org and API quota info |

## Local Development

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Access to a Salesforce sandbox (for integration testing)

### Quick Start

```bash
# Clone the repository
git clone https://github.com/Dhyan011/Black-Diamond-salesforce-service.git
cd Black-Diamond-salesforce-service

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template and configure
cp .env.example .env
# Edit .env with your local settings

# Run with Docker Compose (recommended)
docker-compose up --build

# Or run directly
python -m app.main
```

### Running Tests

```bash
# Unit tests
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v

# All tests with coverage
pytest --cov=app tests/ -v
```

## Deployment

The service is deployed via **Nomad** with Docker containers. See `nomad/` directory for environment-specific HCL files:

- `nomad/dev/salesforce-service.hcl` — Development (port 5710)
- `nomad/stage/salesforce-service.hcl` — Staging (port 5711)
- `nomad/prod/salesforce-service.hcl` — Production (port 5712)

## Environment Variables

All configuration is managed via environment variables injected from HashiCorp Vault. See the Technical Design Document for the complete variable reference.

| Category | Key Variables |
|---|---|
| Flask | `FLASK_ENV`, `PORT`, `SECRET_KEY` |
| Salesforce | `SF_CONSUMER_KEY`, `SF_PRIVATE_KEY_PEM`, `SF_USERNAME`, `SF_LOGIN_URL` |
| Database | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` |
| MinIO | `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET` |
| Security | `HMAC_ENABLED`, `HMAC_SECRET_KEY_CORE`, `HMAC_SECRET_KEY_ENGINEER` |
| PII | `PII_MASKING_ENABLED`, `PII_SERVICE_URL` |

---

*Maintained by Glynac Engineering*
