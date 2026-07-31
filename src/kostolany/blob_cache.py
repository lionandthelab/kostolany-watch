"""Persist cache files to GCS so Cloud Run revisions keep warm data.

Local disk remains the hot path; GCS is the durable layer (6h product TTL
still applies via file mtime / envelope timestamps).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()
_bucket_name: str | None = None
_disabled = False


def _bucket() -> str | None:
    global _bucket_name, _disabled
    if _disabled:
        return None
    if _bucket_name is not None:
        return _bucket_name or None
    name = (os.environ.get("GCS_CACHE_BUCKET") or "").strip()
    _bucket_name = name
    if not name:
        _disabled = True
        return None
    return name


def _gcs_client():
    global _client, _disabled
    if _disabled:
        return None
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        if not _bucket():
            return None
        try:
            from google.cloud import storage  # type: ignore[import-untyped]

            _client = storage.Client()
            return _client
        except Exception as exc:  # noqa: BLE001
            log.warning("GCS client unavailable: %s", exc)
            _disabled = True
            return None


def pull_if_missing(local: Path) -> bool:
    """If local file missing, download from gs://bucket/cache/<name>. Returns True if local exists after."""
    if local.exists():
        return True
    bucket_name = _bucket()
    client = _gcs_client()
    if not bucket_name or client is None:
        return False
    blob_path = f"cache/{local.name}"
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local))
        return local.exists()
    except Exception as exc:  # noqa: BLE001
        log.warning("GCS pull failed %s: %s", blob_path, exc)
        return False


def push_async(local: Path) -> None:
    """Upload local cache file to GCS in a daemon thread."""
    push_blob_async(local, f"cache/{local.name}", content_type="application/json")


def pull_blob(local: Path, blob_path: str) -> bool:
    """Download gs://bucket/<blob_path> if local is missing. Returns True if local exists after."""
    if local.exists():
        return True
    bucket_name = _bucket()
    client = _gcs_client()
    if not bucket_name or client is None:
        return False
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local))
        return local.exists()
    except Exception as exc:  # noqa: BLE001
        log.warning("GCS pull failed %s: %s", blob_path, exc)
        return False


def push_blob_async(
    local: Path,
    blob_path: str,
    *,
    content_type: str = "application/octet-stream",
) -> None:
    """Upload local file to gs://bucket/<blob_path> in a daemon thread."""
    bucket_name = _bucket()
    if not bucket_name or not local.exists():
        return

    def _run() -> None:
        client = _gcs_client()
        if client is None:
            return
        try:
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            blob.upload_from_filename(str(local), content_type=content_type)
        except Exception as exc:  # noqa: BLE001
            log.warning("GCS push failed %s: %s", blob_path, exc)

    threading.Thread(target=_run, name=f"gcs-push-{local.name}", daemon=True).start()
