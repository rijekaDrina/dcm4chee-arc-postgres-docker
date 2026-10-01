#!/usr/bin/env python3
"""Register the public WildFly console callback (IP-based deployment)."""
from pathlib import Path
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

root = Path(__file__).resolve().parents[1]
env = dict(line.strip().split("=", 1) for line in (root / ".env").read_text().splitlines() if "=" in line)
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
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Keycloak Admin API HTTP {error.code} for {method} {path}") from None

form = urllib.parse.urlencode({
    "client_id": "admin-cli",
    "username": env["KEYCLOAK_ADMIN"],
    "password": env["KEYCLOAK_ADMIN_PASSWORD"],
    "grant_type": "password",
}).encode()
token_request = urllib.request.Request(
    base + "/realms/master/protocol/openid-connect/token",
    data=form, headers={"Content-Type": "application/x-www-form-urlencoded"},
)
with urllib.request.urlopen(token_request, context=ctx, timeout=30) as response:
    token = json.loads(response.read())["access_token"]

clients = request("/admin/realms/dcm4che/clients?clientId=wildfly-console", token=token)[1]
if len(clients) != 1:
    raise RuntimeError("WildFly console client not found.")
client = clients[0]
client["redirectUris"] = sorted(set(client.get("redirectUris", []))
                                | {f"https://{env['PUBLIC_HOST']}:9993/console/*"})
client["webOrigins"] = sorted(set(client.get("webOrigins", []))
                              | {f"https://{env['PUBLIC_HOST']}:9993"})
request(f"/admin/realms/dcm4che/clients/{client['id']}", "PUT", client, token)
print("WildFly console callback registru za javnu adresu.")
