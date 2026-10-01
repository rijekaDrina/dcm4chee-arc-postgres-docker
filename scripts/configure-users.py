#!/usr/bin/env python3
"""Rotate built-in PACS accounts and register the IP-based browser callback."""
from __future__ import annotations
from pathlib import Path
import json
import secrets
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

root = Path(__file__).resolve().parents[1]
env = dict(line.strip().split("=", 1) for line in (root / ".env").read_text().splitlines() if "=" in line)
ctx = ssl.create_default_context(cafile=str(root / "certs/ca.crt"))
ctx.check_hostname = False  # IP address access while validating the internal CA chain.
base = f"https://{env['PUBLIC_HOST']}:8843"

def request(path: str, method: str = "GET", data=None, token: str | None = None):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
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
with urllib.request.urlopen(token_request, context=ctx, timeout=20) as response:
    token = json.loads(response.read())["access_token"]

# Allow the public HTTPS callback used by browsers (IP-based access).
ui_clients = request("/admin/realms/dcm4che/clients?clientId=dcm4chee-arc-ui", token=token)[1]
if len(ui_clients) != 1:
    raise RuntimeError("Keycloak UI client dcm4chee-arc-ui not found.")
ui_client = ui_clients[0]
redirect_uris = set(ui_client.get("redirectUris", []))
redirect_uris.update({
    f"https://{env['PUBLIC_HOST']}:8443/dcm4chee-arc/ui2",
    f"https://{env['PUBLIC_HOST']}:8443/dcm4chee-arc/ui2/*",
})
if redirect_uris != set(ui_client.get("redirectUris", [])):
    ui_client["redirectUris"] = sorted(redirect_uris)
    request(f"/admin/realms/dcm4che/clients/{ui_client['id']}", "PUT", ui_client, token)

# The UI stores its selected locale in Keycloak user attribute
# attributes.locale[0]. Users are LDAP-backed, so map that attribute to the
# standard inetOrgPerson preferredLanguage field or Keycloak drops the value.
ldap_providers = request(
    "/admin/realms/dcm4che/components?type=org.keycloak.storage.UserStorageProvider",
    token=token,
)[1]
ldap = next((component for component in ldap_providers if component.get("providerId") == "ldap"), None)
if ldap is None:
    raise RuntimeError("Nije pronađen LDAP provajder za Keycloak realm dcm4che.")
ldap_mappers = request(f"/admin/realms/dcm4che/components?parent={ldap['id']}", token=token)[1]
if not any(mapper.get("name") == "preferred language" for mapper in ldap_mappers):
    request("/admin/realms/dcm4che/components", "POST", {
        "name": "preferred language",
        "providerId": "user-attribute-ldap-mapper",
        "providerType": "org.keycloak.storage.ldap.mappers.LDAPStorageMapper",
        "parentId": ldap["id"],
        "config": {
            "ldap.attribute": ["preferredLanguage"],
            "is.mandatory.in.ldap": ["false"],
            "read.only": ["false"],
            "always.read.value.from.ldap": ["false"],
            "user.model.attribute": ["locale"],
        },
    }, token=token)

# Keycloak 25 only exposes declared profile attributes in userProfileMetadata.
# Declare locale as a self-editable profile field so the UI's language switcher
# can persist its attributes.locale[0] value instead of silently dropping it.
profile = request("/admin/realms/dcm4che/users/profile", token=token)[1]
if not any(attribute.get("name") == "locale" for attribute in profile.get("attributes", [])):
    profile.setdefault("attributes", []).append({
        "name": "locale",
        "displayName": "Jezik interfejsa",
        "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]},
        "multivalued": False,
    })
    request("/admin/realms/dcm4che/users/profile", "PUT", profile, token)

extra_admin = env.get("PACS_ADMIN_USER", "admin")
if not extra_admin.replace("_", "").replace("-", "").isalnum():
    raise RuntimeError("PACS_ADMIN_USER has invalid characters.")
account_names = ["root", "admin", "user"]
if extra_admin not in account_names:
    account_names.append(extra_admin)
passwords = {name: secrets.token_urlsafe(24) for name in account_names}

if extra_admin not in ("root", "admin", "user"):
    matches = request(f"/admin/realms/dcm4che/users?username={extra_admin}&exact=true", token=token)[1]
    if not matches:
        request("/admin/realms/dcm4che/users", "POST", {
            "username": extra_admin, "enabled": True,
            "firstName": extra_admin,
            "credentials": [{"type": "password", "value": passwords[extra_admin], "temporary": False}],
        }, token)
        matches = request(f"/admin/realms/dcm4che/users?username={extra_admin}&exact=true", token=token)[1]
    if len(matches) != 1 or not matches[0].get("federationLink"):
        raise RuntimeError(f"PACS administrator {extra_admin} is not LDAP-backed.")
    target_id = matches[0]["id"]
    roots = request("/admin/realms/dcm4che/users?username=root&exact=true", token=token)[1]
    if len(roots) != 1:
        raise RuntimeError("Built-in root account not found.")
    root_id = roots[0]["id"]
    realm_roles = request(f"/admin/realms/dcm4che/users/{root_id}/role-mappings/realm", token=token)[1]
    request(f"/admin/realms/dcm4che/users/{target_id}/role-mappings/realm", "POST", realm_roles, token)
    mgmt_clients = request("/admin/realms/dcm4che/clients?clientId=realm-management", token=token)[1]
    if len(mgmt_clients) != 1:
        raise RuntimeError("realm-management client not found.")
    mgmt_id = mgmt_clients[0]["id"]
    mgmt_roles = request(f"/admin/realms/dcm4che/users/{root_id}/role-mappings/clients/{mgmt_id}", token=token)[1]
    request(f"/admin/realms/dcm4che/users/{target_id}/role-mappings/clients/{mgmt_id}", "POST", mgmt_roles, token)

# Keycloak JS loadUserProfile() needs these account roles or the UI hides the
# user menu and stops attaching bearer tokens.
account_clients = request("/admin/realms/dcm4che/clients?clientId=account", token=token)[1]
account_id = account_clients[0]["id"]
account_roles = request(f"/admin/realms/dcm4che/clients/{account_id}/roles", token=token)[1]
profile_roles = [role for role in account_roles if role["name"] in ("manage-account", "view-profile")]

for name, password in passwords.items():
    matches = request(f"/admin/realms/dcm4che/users?username={name}&exact=true", token=token)[1]
    if len(matches) != 1:
        raise RuntimeError(f"Cannot unambiguously find the PACS account {name}.")
    account_id = matches[0]['id']
    account = request(f"/admin/realms/dcm4che/users/{account_id}", token=token)[1]
    if not account.get('attributes', {}).get('locale'):
        account.setdefault('attributes', {})['locale'] = ['sr']
        request(f"/admin/realms/dcm4che/users/{account_id}", "PUT", account, token)
    request(f"/admin/realms/dcm4che/users/{matches[0]['id']}/reset-password", "PUT", {
        "type": "password", "value": password, "temporary": False,
    }, token)
    # enforce the account profile roles for every built-in account
    request(f"/admin/realms/dcm4che/users/{matches[0]['id']}/role-mappings/clients/{account_id}",
            "POST", profile_roles, token)
    # rotate the LDAP-side password too (Keycloak caches credentials)
    subprocess.run([
        "docker", "compose", "exec", "-T", "ldap", "ldappasswd", "-x", "-H", "ldap://localhost",
        "-D", "cn=admin,dc=dcm4che,dc=org", "-w", "secret", "-s", password,
        f"uid={name},ou=users,dc=dcm4che,dc=org",
    ], check=True, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # verify OIDC login with the new password
    form = urllib.parse.urlencode({
        "client_id": "dcm4chee-arc-ui", "username": name,
        "password": password, "grant_type": "password",
    }).encode()
    login = urllib.request.Request(
        base + "/realms/dcm4che/protocol/openid-connect/token",
        data=form, headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(login, context=ctx, timeout=20) as response:
        login_result = json.loads(response.read())
        if "access_token" not in login_result:
            raise RuntimeError(f"OIDC login failed for {name}.")
    profile_status, profile = request(
        "/realms/dcm4che/account", token=login_result["access_token"])
    if profile_status != 200 or profile.get("username") != name:
        raise RuntimeError(f"UI user profile not available for {name}.")

record = [
    "Initial DCM4CHEE test credentials (realm dcm4che). Keep private, mode 600.",
    "",
    "Keycloak master admin and service credentials:",
]
record.extend(f"{key}={value}" for key, value in env.items())
record.extend(["", "PACS accounts:"])
for name, password in passwords.items():
    record.append(f"username={name}; password={password}")
cred = root / "secrets/INITIAL_CREDENTIALS.txt"
cred.write_text("\n".join(record) + "\n", encoding="utf-8")
cred.chmod(0o600)
print("Rotirane su lozinke početnih PACS naloga i verifikovana OIDC prijava za svaki.")
