# Authentication for Vertex Video Playground

This project can authenticate to Google Cloud in two ways.

## Option A: ADC (recommended for local development)

On a machine that has `gcloud` installed:

```bash
gcloud auth application-default login
```

Then set:

```bash
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_GENAI_USE_VERTEXAI=True
```

## Option B: Service account key (recommended for servers)

1. Create a service account in your GCP project.
2. Grant it:
   - `roles/aiplatform.user`
   - `roles/storage.objectAdmin` on the output bucket
3. Download a JSON key.
4. Set:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
export GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_GENAI_USE_VERTEXAI=True
```

## Verify

Run:

```bash
source .venv2/bin/activate
python check_auth.py
```

If auth is working, the script prints `status: ok` and the credential type.
