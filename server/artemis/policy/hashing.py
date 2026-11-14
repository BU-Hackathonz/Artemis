"""What an approval binds to (design C1).

The gateway hashes when it asks and again right before it executes. If the two differ, the
proposal or the files under it changed while the user was deciding.
"""

import hashlib
import json


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_payload(files: list[tuple[str, bytes | None, str]]) -> list:
    """`files` is (path, bytes on disk or None if the file is new, new content)."""
    return sorted(
        [path, sha256_hex(old or b""), sha256_hex(new.encode("utf-8"))]
        for path, old, new in files
    )


def run_payload(entry: str, args: list[str], entry_bytes: bytes) -> list:
    return [entry, list(args), sha256_hex(entry_bytes)]


def approval_hash(kind: str, workspace_id: str, payload: list) -> str:
    canonical = json.dumps(
        {"kind": kind, "workspace_id": workspace_id, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + sha256_hex(canonical.encode("utf-8"))
