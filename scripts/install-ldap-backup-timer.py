#!/usr/bin/env python3
"""Install a daily systemd timer for private LDIF exports."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
UNIT_DIR = Path('/etc/systemd/system')


def main() -> None:
    if os.geteuid():
        raise SystemExit('Run as root: sudo python3 scripts/install-ldap-backup-timer.py')
    if '\n' in str(PROJECT) or ' ' in str(PROJECT):
        raise SystemExit('Project path cannot contain whitespace for this systemd unit')
    service = UNIT_DIR / 'dcm4chee-ldap-backup.service'
    timer = UNIT_DIR / 'dcm4chee-ldap-backup.timer'
    service.write_text(f'''[Unit]
Description=Export dcm4chee LDAP configuration and directory to LDIF
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart={sys.executable} {PROJECT}/scripts/backup-ldap.py
''')
    timer.write_text('''[Unit]
Description=Daily dcm4chee LDAP LDIF backup

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
RandomizedDelaySec=15m
Unit=dcm4chee-ldap-backup.service

[Install]
WantedBy=timers.target
''')
    service.chmod(0o644)
    timer.chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', timer.name], check=True)
    print(f'Daily LDAP backup enabled. Files: {PROJECT.parent / "ldap-backups"}')


if __name__ == '__main__':
    main()
