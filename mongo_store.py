#!/usr/bin/env python3
"""Shared MongoDB helpers for the video playground scripts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import MongoClient


@dataclass(frozen=True)
class MongoSettings:
    uri: str
    db_name: str
    user_id: ObjectId


def parse_object_id(value: str | ObjectId | None) -> ObjectId | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    text = str(value).strip()
    if not text:
        return None
    return ObjectId(text)


def resolve_settings(
    uri: str | None = None,
    db_name: str | None = None,
    user_id: str | ObjectId | None = None,
) -> MongoSettings | None:
    resolved_uri = (uri or os.getenv("MONGODB_URI") or "").strip()
    resolved_db = (db_name or os.getenv("MONGODB_DB") or "").strip()
    resolved_user = parse_object_id(user_id or os.getenv("MONGODB_USER_ID"))
    if not resolved_uri or not resolved_db or resolved_user is None:
        return None
    return MongoSettings(uri=resolved_uri, db_name=resolved_db, user_id=resolved_user)


def connect(uri: str, db_name: str):
    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    client.admin.command("ping")
    return client, client[db_name]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_project_doc(
    *,
    project_id: str,
    name: str,
    description: str,
    user_id: ObjectId,
    timestamp: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": project_id,
        "name": name,
        "description": description,
        "userId": user_id,
        "timestamp": timestamp or utc_now(),
        "__v": 0,
    }
    if extra:
        doc.update(extra)
    return doc


def build_mediaasset_doc(
    *,
    prompt: str,
    model: str,
    aspect_ratio: str,
    duration_seconds: int | None,
    local_path: str | None,
    gcs_uri: str | None,
    operation_name: str,
    project_env_id: str,
    user_id: ObjectId,
    batch_queue_item_id: str | None = None,
    is_character: bool = False,
    watermarked: bool = False,
    timestamp: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "aspectRatio": aspect_ratio,
        "durationSeconds": duration_seconds,
        "localPath": local_path,
        "gcsUri": gcs_uri,
        "operationName": operation_name,
        "projectEnvId": project_env_id,
        "userId": user_id,
        "batchQueueItemId": batch_queue_item_id,
        "isCharacter": is_character,
        "watermarked": watermarked,
        "timestamp": timestamp or utc_now(),
    }
    if extra:
        doc.update(extra)
    return doc


def upsert_project(collection, doc: dict[str, Any]):
    return collection.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)


def upsert_mediaasset(collection, doc: dict[str, Any]):
    filter_doc = {"operationName": doc["operationName"]}
    return collection.update_one(filter_doc, {"$set": doc}, upsert=True)
