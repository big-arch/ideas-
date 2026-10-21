"""Download the publisher's GGUF and verify its published LFS SHA256."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    root = Path(__file__).resolve().parent / "runtime" / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    repo = "https://huggingface.co/api/models/Qwen/Qwen3-4B-GGUF/tree/main"
    with urllib.request.urlopen(repo, timeout=30) as response:
        rows = json.load(response)
    item = next(row for row in rows if row["path"] == "Qwen3-4B-Q4_K_M.gguf")
    target = root / item["path"]
    expected = item["lfs"]["oid"]
    if target.exists() and target.stat().st_size == item["size"]:
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual == expected:
            print("Publisher GGUF already present and checksum verified.")
            return
    if shutil.disk_usage(root).free < item["size"] + 2 * 1024**3:
        raise RuntimeError("Insufficient free disk space for model plus reserve")
    print("Downloading official Qwen GGUF: 2.5 GB", flush=True)
    url = "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/" + item["path"]
    digest = hashlib.sha256()
    size = 0
    progress = 10
    with urllib.request.urlopen(url, timeout=90) as response, target.open("wb") as stream:
        while block := response.read(2 * 1024**2):
            stream.write(block)
            digest.update(block)
            size += len(block)
            if size * 100 / item["size"] >= progress:
                print(f"GGUF: {size * 100 / item['size']:.0f}%", flush=True)
                progress += 10
    if size != item["size"] or digest.hexdigest() != expected:
        raise RuntimeError("Publisher GGUF checksum mismatch; model import is forbidden")
    (root / "qwen-source.json").write_text(json.dumps({"repository": repo, "sha256": expected, "size": size}), encoding="utf-8")
    print("Publisher GGUF SHA256 verified.", flush=True)


if __name__ == "__main__":
    main()
