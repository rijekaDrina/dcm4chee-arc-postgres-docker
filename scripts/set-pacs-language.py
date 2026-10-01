#!/usr/bin/env python3
"""Set a PACS account's preferred UI language without changing its password."""
import argparse
import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines() if '=' in line)
base = f"https://{env['PUBLIC_HOST']}:8843"
context = ssl.create_default_context(cafile=str(root / 'certs/ca.crt'))

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('username')
parser.add_argument('language', choices=('sr', 'sr-Cyrl', 'en'))
args = parser.parse_args()

form = urllib.parse.urlencode({
    'client_id': 'admin-cli', 'grant_type': 'password',
    'username': env['KEYCLOAK_ADMIN_USER'], 'password': env['KEYCLOAK_ADMIN_PASSWORD'],
}).encode()
with urllib.request.urlopen(urllib.request.Request(
        base + '/realms/master/protocol/openid-connect/token', form,
        {'Content-Type': 'application/x-www-form-urlencoded'}), context=context) as response:
    token = json.load(response)['access_token']


def call(path, method='GET', payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {'Authorization': 'Bearer ' + token}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    with urllib.request.urlopen(urllib.request.Request(base + path, body, headers, method=method),
                                context=context) as response:
        return json.load(response) if response.status != 204 else None


query = urllib.parse.urlencode({'username': args.username, 'exact': 'true'})
accounts = call('/admin/realms/dcm4che/users?' + query)
if len(accounts) != 1 or accounts[0]['username'] != args.username:
    raise SystemExit('Expected exactly one PACS account with that username.')
path = '/admin/realms/dcm4che/users/' + accounts[0]['id']
account = call(path)
account.setdefault('attributes', {})['locale'] = [args.language]
call(path, 'PUT', account)
updated = call(path)
if updated.get('attributes', {}).get('locale') != [args.language]:
    raise SystemExit('Keycloak did not retain the selected language.')
print(f"{args.username}: {args.language} (saved in Keycloak and LDAP)")
