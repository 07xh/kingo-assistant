#!/usr/bin/env python3
"""Read-only audit of publicly reachable Git history. Never print secret values.

Scans unique text blobs from all locally fetched git refs, including old blob
versions. Patterns identify *candidates*, not proof of a live credential.
No network calls or files written by this script.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import subprocess
import sys

MAX_BLOB_BYTES = 600_000
TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".har", ".txt", ".md",
                 ".sh", ".bat", ".ps1", ".toml", ".cfg", ".xml", ".ini",
                 ".http", ".js", ".ts", ".csv", ".properties", ".html",
                 ".htm", ".sql", ".gradle", ".kt", ".java"}
SAFE_VALUE_WORDS = ("example", "fake", "fictional", "synthetic", "mock",
                    "dummy", "placeholder", "test-only", "not-real",
                    "redacted", "sample", "changeme", "change-me",
                    "your-", "demo", "123456", "password", "xxx")

HIGH_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|github_pat_[A-Za-z0-9_]{50,}"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "google_api_key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
}
POTENTIAL_PATTERNS = {
    "password_literal": re.compile(
        rb'''(?im)(?:"|')?(?:password|passwd|pwd|passphrase|login_password)(?:"|')?\s*[:=]\s*(?:"([^"\r\n]{6,})"|'([^'\r\n]{6,})')'''
    ),
    "authorization_header": re.compile(
        rb'''(?im)(?:authorization|proxy-authorization)["']?\s*[:=]\s*["']?(?:Bearer|Basic)\s+([A-Za-z0-9_\-+.=/]{20,})'''
    ),
    "session_cookie": re.compile(
        rb'''(?im)(?:JSESSIONID|PHPSESSID|sessionid)\s*=\s*([A-Za-z0-9._%-]{12,})'''
    ),
    "har_cookie_field": re.compile(
        rb'''(?is)"name"\s*:\s*"Cookie"\s*,\s*"value"\s*:\s*"([^"]{12,})"'''
    ),
    "embedded_url_auth": re.compile(rb'''https?://[^\s/@:]{3,}:[^\s/@]{8,}@'''),
}


def git(*args: str, input_data: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], input=input_data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True).stdout


def is_candidate_path(path: str) -> bool:
    p = Path(path)
    return (p.suffix.lower() in TEXT_SUFFIXES
            or p.name.lower().startswith(".env")
            or p.name.lower() in {".npmrc", ".pypirc", ".netrc"})


def rejected_dummy(value: bytes) -> bool:
    value = value.strip().strip(b"'\"")
    lower = value.lower().decode("utf-8", errors="replace")
    return (len(value) < 6 or any(word in lower for word in SAFE_VALUE_WORDS)
            or ("{" in lower or "}" in lower) or " " in lower
            or "$" in lower or "<" in lower or ">" in lower)


def scan(content: bytes) -> set[str]:
    found: set[str] = set()
    for name, regex in HIGH_PATTERNS.items():
        if regex.search(content):
            found.add(name)
    for name, regex in POTENTIAL_PATTERNS.items():
        for match in regex.finditer(content):
            value = next((v for v in match.groups() if v is not None), b"")
            if name == "embedded_url_auth" or not rejected_dummy(value):
                found.add("candidate_" + name)
                break
    return found


def read_blobs(hashes: list[str]) -> dict[str, bytes]:
    payload = ("\n".join(hashes) + "\n").encode("ascii")
    stream = git("cat-file", "--batch", input_data=payload)
    result: dict[str, bytes] = {}
    offset = 0
    for requested in hashes:
        end = stream.index(b"\n", offset)
        sha, kind, size_bytes = stream[offset:end].split()
        size = int(size_bytes)
        offset = end + 1
        if kind == b"blob":
            result[requested] = stream[offset:offset + size]
        offset += size + 1
    return result


def main() -> int:
    refs = git("for-each-ref", "--format=%(refname)").decode().splitlines()
    tips = Counter("pull_request" if "/pr/" in r else
                   "branch" if "/heads/" in r or "/origin/" in r else
                   "tag" if "/tags/" in r else "other"
                   for r in refs)
    names: dict[str, str] = {}
    for line in git("rev-list", "--objects", "--all").decode("utf-8", errors="replace").splitlines():
        sha, _, path = line.partition(" ")
        if path and is_candidate_path(path):
            names.setdefault(sha, path)
    if not names:
        print("AUDIT_ERROR no text blobs enumerated")
        return 2

    keys = list(names)
    batch_check = git("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                      input_data=("\n".join(keys) + "\n").encode())
    readable = []
    for line in batch_check.decode().splitlines():
        sha, kind, size = line.split()
        if kind == "blob" and int(size) <= MAX_BLOB_BYTES:
            readable.append(sha)
    results: dict[str, set[str]] = {}
    # Memory-constrained streaming in batches. Do not print matching content.
    for i in range(0, len(readable), 40):
        for sha, blob in read_blobs(readable[i:i + 40]).items():
            if b"\x00" in blob[:512]:
                continue
            hits = scan(blob)
            if hits:
                results[sha] = hits
    tally = Counter(x for hits in results.values() for x in hits)
    print(f"AUDIT_REFS branches={tips['branch']} pull_requests={tips['pull_request']} tags={tips['tag']}")
    print(f"AUDIT_OBJECTS candidate_text_versions={len(keys)} scanned={len(readable)} findings={len(results)}")
    print("AUDIT_COUNTS " + (" ".join(f"{k}={v}" for k, v in sorted(tally.items()))
                              if tally else "none"))
    # File path and finding class only; never emit secret snippets, byte offsets,
    # raw hash values of credentials, commit messages or account data.
    for sha in sorted(results):
        classes = ",".join(sorted(results[sha]))
        path = names[sha].replace("\t", "_").replace("\n", "_")
        print(f"AUDIT_FINDING path={path} blob_sha={sha} classes={classes}")
    print("AUDIT_SCOPE all locally fetched reachable branch/tag/PR refs; "
          "excludes external clones, GitHub caches and unreachable objects")
    if any(any(not x.startswith("candidate_") for x in hits) for hits in results.values()):
        return 1
    # Candidate-only detections remain a manual-review result, not an all-clear.
    return 0


if __name__ == "__main__":
    sys.exit(main())
