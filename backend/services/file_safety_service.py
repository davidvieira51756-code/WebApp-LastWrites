from __future__ import annotations

import os
import re
from pathlib import PurePath
from typing import Iterable
from uuid import uuid4

CONTROL_CHARS_REGEX = re.compile(r"[\x00-\x1f\x7f]+")
SAFE_FILENAME_REGEX = re.compile(r"[^A-Za-z0-9._ -]+")

DEFAULT_ALLOWED_EXTENSIONS = {
    ".txt",
    ".pdf",
    ".md",
    ".json",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}
DEFAULT_ALLOWED_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/pdf",
    "application/json",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
}
DEFAULT_ALLOWED_CONTENT_TYPE_PREFIXES = ("text/", "image/")


def _parse_csv_env(value: str, *, fallback: Iterable[str]) -> set[str]:
    parts = {part.strip().lower() for part in value.split(",") if part.strip()}
    return parts or {part.lower() for part in fallback}


def max_upload_size_bytes() -> int:
    raw_value = os.getenv("UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)).strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        return 10 * 1024 * 1024
    return parsed if parsed > 0 else 10 * 1024 * 1024


def sanitize_filename(file_name: str) -> str:
    base_name = PurePath(file_name or "").name
    base_name = CONTROL_CHARS_REGEX.sub("", base_name).strip().strip(". ")
    base_name = SAFE_FILENAME_REGEX.sub("_", base_name)
    base_name = re.sub(r"\s+", " ", base_name).strip()

    if not base_name or base_name in {".", ".."}:
        return f"file-{uuid4().hex}.bin"

    if len(base_name) <= 180:
        return base_name

    stem, dot, suffix = base_name.rpartition(".")
    if dot and suffix:
        suffix = f".{suffix[:20]}"
        stem = stem[: max(1, 180 - len(suffix))]
        return f"{stem}{suffix}"
    return base_name[:180]


def validate_upload(file_name: str, content_type: str | None, file_size_bytes: int) -> None:
    if file_size_bytes <= 0:
        raise ValueError("Uploaded file is empty.")

    max_bytes = max_upload_size_bytes()
    if file_size_bytes > max_bytes:
        raise ValueError(f"Uploaded file exceeds the maximum size of {max_bytes} bytes.")

    sanitized_name = sanitize_filename(file_name)
    extension = PurePath(sanitized_name).suffix.lower()
    allowed_extensions = _parse_csv_env(
        os.getenv("ALLOWED_UPLOAD_EXTENSIONS", ""),
        fallback=DEFAULT_ALLOWED_EXTENSIONS,
    )
    if extension not in allowed_extensions:
        raise ValueError("Uploaded file type is not allowed.")

    normalized_content_type = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    allowed_content_types = _parse_csv_env(
        os.getenv("ALLOWED_UPLOAD_CONTENT_TYPES", ""),
        fallback=DEFAULT_ALLOWED_CONTENT_TYPES,
    )
    allowed_prefixes = tuple(
        _parse_csv_env(
            os.getenv("ALLOWED_UPLOAD_CONTENT_TYPE_PREFIXES", ""),
            fallback=DEFAULT_ALLOWED_CONTENT_TYPE_PREFIXES,
        )
    )

    if normalized_content_type not in allowed_content_types and not any(
        normalized_content_type.startswith(prefix) for prefix in allowed_prefixes
    ):
        raise ValueError("Uploaded content type is not allowed.")
