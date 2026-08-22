from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from generate_video import VideoJob, build_reference_images, run_video_job


@dataclass
class _FakeVideo:
    uri: str | None


@dataclass
class _FakeVideoWrapper:
    video: _FakeVideo


@dataclass
class _FakeResult:
    generated_videos: list[_FakeVideoWrapper]


class _FakeOperation:
    def __init__(self, *, done: bool, response: object | None, result: _FakeResult, name: str) -> None:
        self.done = done
        self.response = response
        self.result = result
        self.name = name


class _FakeModels:
    def __init__(self, op1: _FakeOperation) -> None:
        self._op1 = op1

    def generate_videos(self, *, model: str, prompt: str, config):  # noqa: ANN001
        return self._op1


class _FakeOperations:
    def __init__(self, op2: _FakeOperation) -> None:
        self._op2 = op2

    def get(self, operation):  # noqa: ANN001
        return self._op2


class _FakeClient:
    def __init__(self, op1: _FakeOperation, op2: _FakeOperation) -> None:
        self.models = _FakeModels(op1)
        self.operations = _FakeOperations(op2)


class _FakeUpsertResult:
    def __init__(self) -> None:
        self.matched_count = 0
        self.modified_count = 0
        self.upserted_id = None


class _FakeCollection:
    def __init__(self) -> None:
        self.docs = []

    def update_one(self, filter_doc, update_doc, upsert=False):  # noqa: ANN001
        self.docs.append((filter_doc, update_doc, upsert))
        return _FakeUpsertResult()

    def find_one(self, *args, **kwargs):  # noqa: ANN001
        return {"_id": "abc123", "id": "abc123", "batchQueueItemId": "abc123"}


class _FakeDb(dict):
    def __getitem__(self, item):
        if item not in self:
            self[item] = _FakeCollection()
        return super().__getitem__(item)


class _FakeMongoClient:
    def close(self):
        return None


class _FakeSettings:
    def __init__(self) -> None:
        self.uri = "mongodb://example"
        self.db_name = "video-studio"
        self.user_id = "6a4264656320d6dd8421deba"


def test_build_reference_images_reads_local_assets(tmp_path: Path) -> None:
    image_path = tmp_path / "commander.png"
    image_path.write_bytes(b"png-bytes")

    references = build_reference_images([str(image_path)])

    assert len(references) == 1
    assert references[0].image.image_bytes == b"png-bytes"
    assert references[0].image.mime_type == "image/png"
    assert references[0].reference_type.value == "ASSET"


def test_run_video_job_falls_back_to_output_gcs_uri_when_vertex_returns_null_uri(monkeypatch, tmp_path: Path) -> None:
    op2 = _FakeOperation(
        done=True,
        response=object(),
        result=_FakeResult(generated_videos=[_FakeVideoWrapper(video=_FakeVideo(uri=None))]),
        name="operations/test-op",
    )
    op1 = _FakeOperation(
        done=False,
        response=None,
        result=_FakeResult(generated_videos=[]),
        name="operations/test-op",
    )
    fake_collection = _FakeCollection()
    fake_db = _FakeDb()
    fake_db["mediaassets"] = fake_collection

    monkeypatch.setattr("generate_video.make_client", lambda job: _FakeClient(op1, op2))
    monkeypatch.setattr("generate_video.resolve_settings", lambda *args, **kwargs: _FakeSettings())
    monkeypatch.setattr("generate_video.connect", lambda uri, db_name: (_FakeMongoClient(), fake_db))

    job = VideoJob(
        project_id="project-123",
        location="global",
        model="veo-3.1-lite-generate-001",
        prompt="test prompt",
        aspect_ratio="16:9",
        duration_seconds=None,
        output_gcs_uri="gs://bucket/hermes/demo/001/",
        poll_seconds=0,
        project_dir=str(tmp_path),
        mongo_uri="mongodb://example",
        mongo_db="video-studio",
        user_id="6a4264656320d6dd8421deba",
        project_env_id="project-env",
        resolution="720p",
        generate_audio=False,
        watermarked=False,
    )

    payload = run_video_job(job, emit_status=False)

    assert payload["videos"][0]["uri"] == "gs://bucket/hermes/demo/001/"
    assert fake_collection.docs
    inserted_doc = fake_collection.docs[0][1]["$set"]
    assert inserted_doc["gcsUri"] == "gs://bucket/hermes/demo/001/"
