#!/usr/bin/env python3
"""Keep a free-space reserve on the DICOM storage (fs1).

Without dcmStorageThreshold the archive keeps writing until the filesystem is
100% full, which breaks WildFly and leaves partially written objects. With it,
dcm4chee marks fs1 as full and rejects new objects cleanly instead.
Exit code 3 means the value changed and the archive must be restarted.
"""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
stat = os.statvfs(root / "data" / "storage")
size_gb = stat.f_blocks * stat.f_frsize / 1024 ** 3
# 2% of the disk, at least 1 GB and at most 50 GB.
threshold = f"{max(1, min(50, int(size_gb * 0.02)))}GB"

LDAP = ["docker", "compose", "exec", "-T", "ldap"]
AUTH = ["-x", "-D", "cn=admin,dc=dcm4che,dc=org", "-w", "secret"]
DN = "dcmStorageID=fs1,dicomDeviceName=dcm4chee-arc,cn=Devices,cn=DICOM Configuration,dc=dcm4che,dc=org"

current = subprocess.run(LDAP + ["ldapsearch", "-LLL", *AUTH, "-b", DN, "-s", "base", "dcmStorageThreshold"],
                         cwd=root, capture_output=True, text=True, check=True).stdout
if f"dcmStorageThreshold: {threshold}" in current:
    print(f"Storage threshold already {threshold}.")
    sys.exit(0)
ldif = f"dn: {DN}\nchangetype: modify\nreplace: dcmStorageThreshold\ndcmStorageThreshold: {threshold}\n"
subprocess.run(LDAP + ["ldapmodify", *AUTH], cwd=root, input=ldif, text=True, check=True)
print(f"Storage threshold set to {threshold} free space ({size_gb:.0f} GB disk).")
sys.exit(3)
