#!/usr/bin/env python3
"""Fetch the official pinned macOS universal CLI; verify SHA-256 before install."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

URL = 'https://github.com/storytold/effectcraft/releases/download/v0.13.0/effectcraft-cli-0.13.0-macos-universal.zip'
SHA256 = '81babf492f2ce35ef9e2c3590d2647b69f507dd0485c7f72a4da426822caca93'
MEMBER = 'effectcraft-cli-0.13.0-macos-universal/effectcraft-cli'
LIMIT = 100 * 1024 * 1024

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Verify an already downloaded official archive')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('This helper installs the macOS universal CLI. On other systems install the official release and set EFFECTCRAFT_CLI.')
    destination = Path(__file__).resolve().parents[1] / '.runtime/bin/effectcraft-cli'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        archive = Path(tmp) / 'cli.zip'
        if args.archive:
            if args.archive.stat().st_size > LIMIT:
                raise ValueError('Archive exceeds size limit')
            shutil.copyfile(args.archive, archive)
        else:
            request = Request(URL, headers={'User-Agent':'effectcraft-codex-bridge/1.0'})
            with urlopen(request, timeout=60) as response, archive.open('wb') as out:
                total = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > LIMIT:
                        raise ValueError('Archive exceeds size limit')
                    out.write(chunk)
        digest = hashlib.sha256()
        with archive.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        if digest.hexdigest() != SHA256:
            raise ValueError('Official CLI archive SHA-256 does not match; no executable was installed')
        with zipfile.ZipFile(archive) as z:
            info = z.getinfo(MEMBER)
            if info.file_size > 250 * 1024 * 1024:
                raise ValueError('Executable exceeds size limit')
            target = Path(tmp) / 'effectcraft-cli'
            with z.open(info) as src, target.open('wb') as out:
                shutil.copyfileobj(src, out)
            target.chmod(0o755)
            os.replace(target, destination)
            for license_name in ['LICENSE-MIT','LICENSE-APACHE']:
                text = z.read('effectcraft-cli-0.13.0-macos-universal/' + license_name)
                (destination.parent / license_name).write_bytes(text)
    print(f'Official EffectCraft 0.13.0 CLI verified and installed: {destination}')
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
