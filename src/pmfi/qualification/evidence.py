from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml


_URL_USERINFO_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+(?::[^/\s@]+)?@", re.IGNORECASE)
_SECRET_MARKERS = ("api_key", "password", "private_key", "bearer ", "authorization")


def sanitize_git_remote(value: str | None) -> str | None:
    if not value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or "@" not in parsed.netloc:
        return value
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def contains_secret_text(manifest_text: str, evidence: dict[str, Any]) -> bool:
    text = manifest_text + "\n" + yaml.safe_dump(evidence, sort_keys=True)
    lowered = text.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS) or bool(_URL_USERINFO_RE.search(text))


def evidence_contains_secret(manifest_path: Path, evidence: dict[str, Any]) -> bool:
    return contains_secret_text(manifest_path.read_text(encoding="utf-8"), evidence)


def schema_fingerprint(sql_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(sql_dir.glob("*.sql")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
