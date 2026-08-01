#!/usr/bin/env python3
"""
delete_project_completely.py

Completely wipes a video project from both the local disk and MongoDB.
Deletes:
  1. The project directory: video_projects/<project_name>/
  2. The database records in 'projects' collection.
  3. All generated media assets in 'mediaassets' collection.
  4. All queued/processed queue jobs in 'queuejobs' collection.

Usage:
  python scripts/delete_project_completely.py --project <project_name>
"""

import os
import sys
import shutil
import json
import argparse
from pathlib import Path
from pymongo import MongoClient

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_PROJECTS_DIR = PROJECT_ROOT / "video_projects"
DEFAULT_MONGO_URI = "mongodb://victor:victoruche22123vic@76.13.42.74:27017/video-studio?directConnection=true&authSource=admin"

def main():
    parser = argparse.ArgumentParser(description="Completely delete a project from local disk and MongoDB.")
    parser.add_argument("--project", required=True, help="Project slug/folder name to delete")
    parser.add_argument("--mongo-uri", default=None, help="Overridden MongoDB connection string")
    parser.add_argument("--skip-db", action="store_true", help="Skip database record deletions, only delete local folder")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    project_name = args.project.strip()
    project_dir = DEFAULT_PROJECTS_DIR / project_name

    if not project_dir.exists():
        print(f"[Error] Local project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"⚠️  WARNING: You are about to COMPLETELY delete the project '{project_name}'!")
    print(f"This will delete:")
    print(f"  - Local folder: {project_dir}")
    if not args.skip_db:
        print(f"  - MongoDB records in 'projects', 'mediaassets', and 'queuejobs'")
    print()

    if not args.force:
        confirm = input(f"Are you sure you want to proceed? Type the project name '{project_name}' to confirm: ")
        if confirm != project_name:
            print("Cleanup cancelled. Project name verification mismatch.")
            sys.exit(0)

    # 1. Database Cleanup
    if not args.skip_db:
        mongo_uri = args.mongo_uri
        mongo_db = "video-studio"
        mongo_project_id = None

        # Try to read credentials from project.json
        project_json_path = project_dir / "project.json"
        if project_json_path.exists():
            try:
                p_json = json.loads(project_json_path.read_text(encoding="utf-8"))
                if not mongo_uri:
                    mongo_uri = p_json.get("mongo_uri")
                mongo_db = p_json.get("mongo_db") or mongo_db
                mongo_project_id = p_json.get("mongo_project_id") or p_json.get("envId")
            except Exception as e:
                print(f"[Warning] Failed to parse project.json: {e}")

        # Fallback to default local Mongo URI if not resolved
        if not mongo_uri:
            mongo_uri = DEFAULT_MONGO_URI

        print(f"\nConnecting to MongoDB database '{mongo_db}'...")
        try:
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            db = client[mongo_db]

            # Resolve project ID by slug if not read from project.json
            if not mongo_project_id:
                p_doc = db.projects.find_one({"slug": project_name})
                if p_doc:
                    mongo_project_id = p_doc.get("envId") or str(p_doc.get("_id"))
                else:
                    # Try querying mediaassets for a sample projectEnvId
                    sample = db.mediaassets.find_one({"prompt": {"$regex": "^001:"}})
                    if sample:
                        mongo_project_id = sample.get("projectEnvId")

            if mongo_project_id:
                print(f"Resolved Project ID / Env ID: {mongo_project_id}")

                # Delete from projects
                p_del = db.projects.delete_many({"$or": [{"slug": project_name}, {"envId": mongo_project_id}]})
                print(f"  Deleted {p_del.deleted_count} record(s) from 'projects' collection.")

                # Delete from mediaassets
                ma_del = db.mediaassets.delete_many({"projectEnvId": mongo_project_id})
                print(f"  Deleted {ma_del.deleted_count} record(s) from 'mediaassets' collection.")

                # Delete from queuejobs
                qj_del = db.queuejobs.delete_many({"projectEnvId": mongo_project_id})
                print(f"  Deleted {qj_del.deleted_count} record(s) from 'queuejobs' collection.")
            else:
                print("[Warning] Could not resolve project ID/slug in MongoDB. Skipping database deletion.")
            
            client.close()
        except Exception as e:
            print(f"[ERROR] Database cleanup failed: {e}")

    # 2. Local Folder Cleanup
    print(f"\nDeleting local directory: {project_dir}...")
    try:
        shutil.rmtree(project_dir)
        print(f"[SUCCESS] Successfully deleted local project folder.")
    except Exception as e:
        print(f"[ERROR] Failed to delete local folder: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nProject '{project_name}' has been completely wiped.")

if __name__ == "__main__":
    main()
