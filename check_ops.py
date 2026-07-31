import os
import json
from google import genai

# Load GCP service account key
with open('gcp-key.json', 'r') as f:
    key_info = json.load(f)

client = genai.Client(vertexai=True, project=key_info['project_id'], location='us-central1')

operations = [
    "projects/project-fb2dc00c-a54a-48bd-884/locations/us-central1/publishers/google/models/veo-3.1-lite-generate-001/operations/f032891f-f51c-4994-8159-ce04cfc91758",
    "projects/project-fb2dc00c-a54a-48bd-884/locations/us-central1/publishers/google/models/veo-3.1-lite-generate-001/operations/4abe3f78-fe5f-481d-b113-ddcf1d32ef12",
    "projects/project-fb2dc00c-a54a-48bd-884/locations/us-central1/publishers/google/models/veo-3.1-lite-generate-001/operations/1e908c8f-2b8b-48f3-8567-223d12951c3a",
    "projects/project-fb2dc00c-a54a-48bd-884/locations/us-central1/publishers/google/models/veo-3.1-lite-generate-001/operations/5df3c8d0-4f72-4cb7-8500-d3656e3afbb0"
]

from google.genai import types

print("Checking operation status on Google Cloud...")
for op_name in operations:
    try:
        op_obj = types.GenerateVideosOperation(name=op_name)
        res = client.operations.get(op_obj)
        print(f"Op: {op_name.split('/')[-1]}")
        print(f"  Done: {getattr(res, 'done', None)}")
        print(f"  Error: {getattr(res, 'error', None)}")
        if hasattr(res, 'response'):
            print(f"  Response: {res.response}")
        print("-" * 50)
    except Exception as e:
        print(f"Op: {op_name.split('/')[-1]} ERROR: {e}")
        print("-" * 50)
