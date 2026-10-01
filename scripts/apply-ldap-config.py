#!/usr/bin/env python3
"""Apply DICOM TLS settings and the WildFly console callback."""
from pathlib import Path
import json
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

root = Path(__file__).resolve().parents[1]
env = dict(line.strip().split("=", 1) for line in (root / ".env").read_text().splitlines() if "=" in line)

# DICOM TLS: Java 21-compatible ciphers on the archive device
subprocess.run(
    ["docker", "compose", "exec", "-T", "ldap", "ldapmodify", "-x",
     "-D", "cn=admin,dc=dcm4che,dc=org", "-w", "secret", "-f", "/dev/stdin"],
    cwd=root,
    input=(root / "scripts" / "enable-dicom-tls.ldif").read_bytes(),
    check=True,
)

# WildFly console callback for browser access via the public IP
ctx = ssl.create_default_context(cafile=str(root / "certs/ca.crt"))
ctx.check_hostname = False
base = f"https://{env['PUBLIC_HOST']}:8843"

def request(path, method="GET", data=None, token=None):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, context=ctx, timeout=20) as response:
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)

form = urllib.parse.urlencode({
    "client_id": "admin-cli", "username": env["KEYCLOAK_ADMIN"],
    "password": env["KEYCLOAK_ADMIN_PASSWORD"], "grant_type": "password",
}).encode()
with urllib.request.urlopen(urllib.request.Request(
        base + "/realms/master/protocol/openid-connect/token", data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"}), context=ctx, timeout=20) as response:
    token = json.loads(response.read())["access_token"]

_, clients = request("/admin/realms/dcm4che/clients?clientId=wildfly-console", token=token)
client = clients[0]
client["redirectUris"] = sorted(set(client.get("redirectUris", [])) |
                                {f"https://{env['PUBLIC_HOST']}:9993/console/*"})
client["webOrigins"] = sorted(set(client.get("webOrigins", [])) |
                              {f"https://{env['PUBLIC_HOST']}:9993"})
request(f"/admin/realms/dcm4che/clients/{client['id']}", "PUT", client, token)
subprocess.run(["python3", str(root / "scripts/configure-ui-languages.py")], cwd=root, check=True)
print("Primljena su DICOM TLS podesavanja, UI jezici i konzola callback.")
