"""Install official portable Ollama in the project, without changing PATH or registry."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    root = Path(__file__).resolve().parent / "runtime"
    root.mkdir(exist_ok=True)
    destination = root / "ollama"
    if (destination / "ollama.exe").exists():
        print("Portable Ollama already present; no automatic update.")
        return
    with urllib.request.urlopen("https://api.github.com/repos/ollama/ollama/releases/latest", timeout=30) as response:
        release = json.load(response)
    asset = next(a for a in release["assets"] if a["name"] == "ollama-windows-amd64.zip")
    expected = asset.get("digest", "")
    if not expected.startswith("sha256:"):
        raise RuntimeError("Official release is missing a SHA256 digest")
    if shutil.disk_usage(root).free < 10 * 1024**3:
        raise RuntimeError("Need at least 10 GiB free before downloading runtime and model")
    archive = root / "ollama-windows-amd64.zip"
    print(f"Downloading official Ollama {release['tag_name']}: {asset['size'] / 1024**2:.0f} MiB", flush=True)
    digest = hashlib.sha256()
    size = 0
    next_progress = 10
    with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as response, archive.open("wb") as stream:
        while block := response.read(2 * 1024**2):
            digest.update(block)
            stream.write(block)
            size += len(block)
            percent = size * 100 / asset["size"]
            if percent >= next_progress:
                print(f"Download: {percent:.0f}%", flush=True)
                next_progress += 10
    if size != asset["size"] or "sha256:" + digest.hexdigest() != expected:
        raise RuntimeError("Official archive checksum or size mismatch; extraction stopped")
    with zipfile.ZipFile(archive) as bundle:
        expanded = sum(info.file_size for info in bundle.infolist())
        if shutil.disk_usage(root).free < expanded + 5 * 1024**3:
            raise RuntimeError("Not enough disk space for extraction, model and reserve")
        for info in bundle.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError("Unsafe path in official archive")
        print(f"Checksum verified. Extracting {expanded / 1024**3:.1f} GiB", flush=True)
        bundle.extractall(destination)
    (root / "ollama-release.json").write_text(json.dumps({"version": release["tag_name"], "sha256": expected}), encoding="utf-8")
    # Remove only the verified downloaded file inside this project's runtime folder.
    archive.unlink()
    print("Portable runtime ready. Download archive removed.", flush=True)


if __name__ == "__main__":
    main()
