#!/usr/bin/env python3
"""Expose English and installed Serbian UI locales in the language selector."""
from pathlib import Path
import base64
import json
import subprocess
import zipfile

root = Path(__file__).resolve().parents[1]
parent = "dcmuiConfigName=default,dicomDeviceName=dcm4chee-arc,cn=Devices,cn=DICOM Configuration,dc=dcm4che,dc=org"
dn = "dcmuiLanguageConfigName=default," + parent
ldap = ["docker", "compose", "exec", "-T", "ldap"]
bind = ["-x", "-D", "cn=admin,dc=dcm4che,dc=org", "-w", "secret"]

env = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines() if '=' in line)
war_path = root / env.get('UI_WAR_PATH', './build/archive-ui.war')
with zipfile.ZipFile(war_path) as war:
    flags = json.loads(war.read("sr/assets/locale/languageCodeBase64Flag.json"))
    has_cyrillic = 'sr-Cyrl/index.html' in war.namelist()
entries = [
    "en|English|English|" + flags["en"]["flag"],
    "sr|Serbian Latin|Srpski (latinica)|" + flags["sr"]["flag"],
]
if has_cyrillic:
    entries.append("sr-Cyrl|Serbian Cyrillic|Српски (ћирилица)|" + flags["sr-Cyrl"]["flag"])
search = subprocess.run(ldap + ["ldapsearch", "-x", "-LLL", "-b", dn, "-s", "base", "objectClass"],
                        cwd=root, capture_output=True)
if search.returncode not in (0, 32):
    raise SystemExit(search.stderr.decode())
exists = search.returncode == 0
lines = ["dn: " + dn]
if exists:
    lines += ["changetype: modify", "replace: dcmLanguages"]
else:
    lines += ["changetype: add", "objectClass: dcmuiLanguageConfig", "dcmuiLanguageConfigName: default"]
for entry in entries:
    lines.append("dcmLanguages:: " + base64.b64encode(entry.encode()).decode())
subprocess.run(ldap + ["ldapmodify"] + bind + ["-f", "/dev/stdin"], cwd=root,
               input=("\n".join(lines) + "\n").encode(), check=True)

# Earlier settings were written on the parent UI config, which this UI version
# does not use for its language selector. Remove only those obsolete attributes.
old = subprocess.run(ldap + ["ldapsearch", "-x", "-LLL", "-b", parent, "-s", "base",
                             "dcmLanguages", "dcmDefaultLanguage"], cwd=root,
                     capture_output=True, check=True).stdout.decode()
remove = [name for name in ("dcmLanguages", "dcmDefaultLanguage") if name + ":" in old]
if remove:
    lines = ["dn: " + parent, "changetype: modify"]
    for name in remove:
        lines += ["delete: " + name, "-"]
    subprocess.run(ldap + ["ldapmodify"] + bind + ["-f", "/dev/stdin"], cwd=root,
                   input=("\n".join(lines) + "\n").encode(), check=True)
print("Podešen UI birač jezika: English, Srpski (latinica)" + (", Српски (ћирилица)." if has_cyrillic else "."))
