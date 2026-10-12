#!/usr/bin/env python3
"""Export the live OpenLDAP configuration and directory as private LDIF files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = PROJECT.parent / 'ldap-backups'


def docker(*args: str, output=None) -> subprocess.CompletedProcess:
    stdout = subprocess.PIPE if output is None else output
    return subprocess.run(['docker', *args], stdout=stdout, stderr=subprocess.PIPE,
                          text=output is None, check=True)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def export(container: str, database: int, path: Path) -> dict[str, str | int]:
    with path.open('xb') as target:
        os.chmod(path, 0o600)
        docker('exec', container, 'slapcat', '-F', '/etc/openldap/slapd.d',
               '-n', str(database), output=target)
        target.flush()
        os.fsync(target.fileno())
    with path.open('rb') as source:
        count = sum(line.startswith(b'dn: ') for line in source)
    if not count:
        raise RuntimeError(f'LDAP database {database} produced no entries')
    return {'file': path.name, 'entries': count, 'sha256': digest(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument('--retain', type=int, default=14, help='number of successful exports to keep')
    args = parser.parse_args()
    if os.geteuid():
        parser.error('run as root (sudo python3 scripts/backup-ldap.py)')
    if args.retain < 1:
        parser.error('--retain must be at least 1')

    os.umask(0o077)
    destination = args.destination.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    container = subprocess.run(
        ['docker', 'compose', '-f', str(PROJECT / 'compose.yaml'), 'ps', '-q', 'ldap'],
        cwd=PROJECT, capture_output=True, text=True, check=True).stdout.strip()
    if not container:
        raise RuntimeError('LDAP container is not running')
    image = docker('inspect', '--format', '{{.Image}}', container).stdout.strip()

    now = datetime.now(timezone.utc)
    name = now.strftime('ldap-%Y%m%dT%H%M%SZ')
    temporary = Path(tempfile.mkdtemp(prefix='.ldap-export-', dir=destination))
    try:
        manifest = {'created_utc': now.isoformat(), 'image_id': image,
                    'config': export(container, 0, temporary / 'config.ldif'),
                    'directory': export(container, 1, temporary / 'directory.ldif')}
        manifest_path = temporary / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        manifest_path.chmod(0o600)
        final = destination / name
        if final.exists():
            raise RuntimeError(f'backup already exists: {final}')
        temporary.rename(final)
    except BaseException:
        shutil.rmtree(temporary)
        raise

    backups = sorted(path for path in destination.glob('ldap-*')
                     if path.is_dir() and (path / 'manifest.json').is_file())
    for old in backups[:-args.retain]:
        shutil.rmtree(old)
    print(f'LDAP backup: {final}')
    print(f"Entries: config={manifest['config']['entries']}, "
          f"directory={manifest['directory']['entries']}")


if __name__ == '__main__':
    main()
