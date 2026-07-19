"""
Salesforce Service — Flask Application Factory

Creates and configures the Flask application with:
- Startup validation (fail-fast per CLAUDE.md)
- Blueprint registration
- CORS configuration
- Error handlers
- Structured logging
"""

import sys
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from flask_restx import Api

from app import __version__, __service_name__
from app.config import SalesforceServiceSettings, validate_settings, get_settings

logger = logging.getLogger(__name__)


def configure_logging(settings: SalesforceServiceSettings) -> None:
    """Configure logging based on environment settings."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    if settings.LOG_FORMAT == "json":
        from pythonjsonlogger import json as json_logger

        handler = logging.StreamHandler()
        formatter = json_logger.JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
        handler.setFormatter(formatter)
        logging.root.handlers = [handler]
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    logging.root.setLevel(log_level)


def create_app() -> Flask:
    """
    Flask application factory.

    1. Validates all configuration (fail-fast)
    2. Configures logging
    3. Sets up CORS
    4. Registers API routes
    5. Registers error handlers
    """
    # ---- Step 1: Validate configuration ----
    errors = validate_settings()
    if errors:
        # Log errors to stderr before dying
        print("=" * 80, file=sys.stderr)
        print("[ERROR] CONFIGURATION VALIDATION FAILED", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        for field, details in errors.items():
            print(f"\n[{field}]", file=sys.stderr)
            print(f"  Error: {details['error']}", file=sys.stderr)
            print(f"  Fix:   {details['fix']}", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        print("Fix the errors above and restart the service.", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        sys.exit(1)

    settings = get_settings()

    # ---- Step 2: Configure logging ----
    configure_logging(settings)
    logger.info(f"Starting {__service_name__} v{__version__} ({settings.ENVIRONMENT})")

    # ---- Step 3: Create Flask app ----
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["DEBUG"] = settings.FLASK_DEBUG

    # Store settings on app for access in routes
    app.config["SETTINGS"] = settings

    # ---- Step 4: Configure CORS ----
    CORS(
        app,
        origins=settings.allowed_origins_list,
        supports_credentials=True,
    )

    # ---- Step 5: Set up Flask-RESTX API ----
    api = Api(
        app,
        version=__version__,
        title=settings.APP_TITLE,
        description="Salesforce Bulk API 2.0 data extraction service for the Glynac pipeline",
        doc="/api/docs",
    )

    # ---- Step 6: Register route namespaces ----
    from app.routes import (
        health_ns,
        scan_ns,
        objects_ns,
        batch_ns,
        key_ns,
        maintenance_ns,
    )

    api.add_namespace(health_ns, path="/api")
    api.add_namespace(key_ns, path="/api/key")
    api.add_namespace(scan_ns, path="/api/scan")
    api.add_namespace(objects_ns, path="/api")
    api.add_namespace(batch_ns, path="/api/batch")
    api.add_namespace(maintenance_ns, path="/api/maintenance")

    # ---- Step 7: Register error handlers ----
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({
            "success": False,
            "error": "Not found",
            "message": "The requested resource does not exist.",
        }), 404

    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal server error: {error}")
        return jsonify({
            "success": False,
            "error": "Internal server error",
            "message": "An unexpected error occurred. Check service logs.",
        }), 500

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify({
            "success": False,
            "error": "Method not allowed",
            "message": "The HTTP method is not allowed for this endpoint.",
        }), 405

    logger.info(f"Service initialized on {settings.HOST}:{settings.PORT}")
    return app


# ---- Direct execution for local development ----
if __name__ == "__main__":
    app = create_app()
    settings = get_settings()
    app.run(
        host=settings.HOST,
        port=settings.PORT,
        debug=settings.FLASK_DEBUG,
    )
