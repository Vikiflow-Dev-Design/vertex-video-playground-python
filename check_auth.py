#!/usr/bin/env python3
"""Check whether Google Cloud auth is available for this project.

This validates Application Default Credentials (ADC) or a service-account key
pointed to by GOOGLE_APPLICATION_CREDENTIALS.

Usage:
  python check_auth.py
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv
import google.auth
from google.auth.transport.requests import Request

load_dotenv()


def main() -> int:
    print(json.dumps({
        "GOOGLE_CLOUD_PROJECT": os.getenv("GOOGLE_CLOUD_PROJECT"),
        "GOOGLE_CLOUD_LOCATION": os.getenv("GOOGLE_CLOUD_LOCATION"),
        "GOOGLE_APPLICATION_CREDENTIALS": os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    }, indent=2))

    try:
        credentials, project_id = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "message": str(exc),
            "hint": "Use ADC (gcloud auth application-default login) or set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON key.",
        }, indent=2), file=sys.stderr)
        return 1

    expiry = getattr(credentials, "expiry", None)
    info = {
        "status": "ok",
        "credential_type": credentials.__class__.__name__,
        "project_id_from_adc": project_id,
        "quota_project_id": getattr(credentials, "quota_project_id", None),
        "service_account_email": getattr(credentials, "service_account_email", None),
        "expiry": expiry.isoformat() if expiry is not None else None,
    }
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
