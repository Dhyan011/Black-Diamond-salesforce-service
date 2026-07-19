# Salesforce Extraction Service - CLAUDE.md

## Build & Run Commands
- Run locally: `docker-compose up --build`
- Run API in dev mode: `FLASK_ENV=development FLASK_APP=app.main:create_app flask run --port 5710`
- Run tests: `pytest`
- Format code: `black .`
- Lint code: `flake8 .`

## Code Style Guidelines
- **Architecture**: Flask-RESTX factory pattern.
- **Typing**: Use strict Python type hints (`list[dict]`, `Optional[str]`).
- **Formatting**: Black (88 chars), Flake8 compliant.
- **Config**: Pydantic models with `sys.exit(1)` on startup validation failure.
- **Security**: Dual-key HMAC required for all internal API communication.
- **Storage**: MinIO/S3 layout: `salesforce-{env}/{org_id}/{scan_id}/{object_name_lower}/page_001.parquet`.
- **Logs**: JSON formatted logs in production for Promtail/Loki parsing.

## Project Structure
- `app/api/`: Flask-RESTX namespaces (health, scan, batch, etc.).
- `app/auth/`: Salesforce JWT Bearer token generation and HMAC verification.
- `app/clients/`: Salesforce Bulk API 2.0 and MinIO wrapper clients.
- `app/models/`: Dataclasses for scan state and job lifecycle tracking.
- `app/services/`: Core business logic (extraction, normalization, deduplication).
- `nomad/`: HashiCorp Nomad deployment configurations.
