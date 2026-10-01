#!/usr/bin/env python3
"""Compile the Serbian Cyrillic UI from the matching official ARC sources."""
import json, shutil, subprocess, urllib.request, zipfile
from pathlib import Path
root = Path(__file__).resolve().parents[1]
source = root / 'build/ui-source'
if not (source / 'angular.json').exists():
    archive = root / 'build/ui-source-5.35.1.zip'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        urllib.request.urlretrieve('https://codeload.github.com/dcm4che/dcm4chee-arc-light/zip/refs/tags/5.35.1', archive)
    with zipfile.ZipFile(archive) as z:
        prefix = 'dcm4chee-arc-light-5.35.1/dcm4chee-arc-ui2/'
        for entry in z.infolist():
            if entry.filename.startswith(prefix) and not entry.is_dir():
                dest = source / entry.filename[len(prefix):]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(entry))
subprocess.run(['python3', str(root / 'scripts/build-cyrillic-locale.py'), '--prepare'], check=True)
translation = source / 'target/dcm4chee-arc-lang/sr-Cyrl.json'
translation.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(root / 'build/sr-Cyrl.json', translation)
config_path = source / 'angular.json'
config = json.loads(config_path.read_text())
project = config['projects']['dcm4chee-arc-ui2']
project['i18n']['locales']['sr-Cyrl'] = {'translation': 'target/dcm4chee-arc-lang/sr-Cyrl.json', 'baseHref': '/dcm4chee-arc/ui2/sr-Cyrl/'}
project['architect']['build']['options']['localize'] = ['sr-Cyrl']
config_path.write_text(json.dumps(config, indent=2) + '\n')
for rel in ['src/app/app.component.ts', 'src/app/helpers/keycloak-service/keycloak.service.ts']:
    path = source / rel
    content = path.read_text().replace(r'(\w{2})', r'([\w-]+)').replace(r'\/ui2\/\w{2}', r'\/ui2\/[\w-]+')
    path.write_text(content)
subprocess.run(['docker', 'run', '--rm', '--name', 'dcm4chee-cyrillic-ui-build', '--memory', '2500m', '--memory-swap', '3500m', '-e', 'NODE_OPTIONS=--max-old-space-size=2048', '-e', 'NG_BUILD_MAX_WORKERS=1', '-v', str(source) + ':/app:Z', '-w', '/app', 'node:24-bookworm', 'sh', '-c', 'yarn install --frozen-lockfile --non-interactive && yarn ng build --configuration production --optimization=true --aot=true'], check=True)
subprocess.run(['python3', str(root / 'scripts/compile-ui-routing.py')], check=True)
subprocess.run(['python3', str(root / 'scripts/build-cyrillic-locale.py')], check=True)
