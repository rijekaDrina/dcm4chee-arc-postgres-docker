#!/usr/bin/env python3
"""Package the reviewed Serbian Cyrillic resources into the pinned UI WAR."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
source = root / 'build/archive-ui.war'
target = root / 'build/archive-ui-cyrillic.war'
locale = root / 'locales/sr-Cyrl.json'
schemas = root / 'locales/sr-Cyrl/assets/schema'
if not source.is_file():
    raise SystemExit('Missing build/archive-ui.war. Extract the official 5.35.1 UI WAR first.')
if not locale.is_file() or not schemas.is_dir():
    raise SystemExit('Missing reviewed locales/sr-Cyrl translation resources.')
translation = json.loads(locale.read_text())
if translation.get('locale') != 'sr-Cyrl':
    raise SystemExit('Cyrillic translation has the wrong locale code.')
with zipfile.ZipFile(source) as original:
    latin = json.loads(original.read('sr.json'))
    latin_schemas = {Path(name).name for name in original.namelist()
                     if name.startswith('sr/assets/schema/') and name.endswith('.json')}
if set(translation['translations']) != set(latin['translations']):
    raise SystemExit('Cyrillic UI translation keys do not match the official Serbian Latin bundle.')
if {path.name for path in schemas.glob('*.json')} != latin_schemas:
    raise SystemExit('Cyrillic JSON Schema file names do not match the official Serbian Latin bundle.')
prepared = root / 'build/sr-Cyrl.json'
prepared.write_text(json.dumps(translation, ensure_ascii=False, indent=2) + '\n')
if '--prepare' in sys.argv:
    print('Prepared reviewed Serbian Cyrillic Angular messages.')
    raise SystemExit(0)
compiled = root / 'build/ui-source/target/webapp/sr-Cyrl'
classes = root / 'build/ui-source/target/classes'
if not (compiled / 'index.html').is_file():
    raise SystemExit('Missing compiled Cyrillic Angular UI. Run compile-cyrillic-ui.py first.')
if not (classes / 'org/dcm4chee/arc/ui2/UrlRewriting.class').is_file():
    raise SystemExit('Missing Cyrillic URL routing class.')


def flags_payload(data: bytes) -> bytes:
    flags = json.loads(data)
    latin_flag = flags['sr']
    latin_flag.update(name='Serbian Latin', nativeName='Srpski (latinica)')
    flags['sr-Cyrl'] = {**latin_flag, 'name': 'Serbian Cyrillic',
                        'nativeName': 'Српски (ћирилица)', 'code': 'sr-Cyrl',
                        'countryCode': 'rs'}
    return (json.dumps(flags, ensure_ascii=False, indent=2) + '\n').encode()


with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
    for info in original.infolist():
        name = info.filename
        if name.startswith('WEB-INF/classes/org/dcm4chee/arc/ui2/UrlRewriting'):
            continue
        data = original.read(name)
        if re.search(r'/main-[^/]+\.js$', name):
            text = data.decode('utf-8')
            text = text.replace(r'dcm4chee-arc\/ui2\/(\w{2})\//gm',
                                r'dcm4chee-arc\/ui2\/([\w-]+)\//gm')
            text = text.replace(r'/\/ui2\/\w{2}/', r'/\/ui2\/[\w-]+/')
            data = text.encode()
        if name.endswith('assets/locale/languageCodeBase64Flag.json'):
            data = flags_payload(data)
        output.writestr(name, data)
        if name == 'sr.json':
            output.writestr('sr-Cyrl.json', prepared.read_bytes())
    for path in sorted(schemas.glob('*.json')):
        output.writestr('sr-Cyrl/assets/schema/' + path.name, path.read_bytes())
    for path in classes.rglob('UrlRewriting*.class'):
        output.writestr('WEB-INF/classes/' + path.relative_to(classes).as_posix(), path.read_bytes())
    for path in sorted(compiled.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(compiled).as_posix()
        if relative.startswith('assets/schema/'):
            continue
        data = path.read_bytes()
        if relative.endswith('assets/locale/languageCodeBase64Flag.json'):
            data = flags_payload(data)
        output.writestr('sr-Cyrl/' + relative, data)
print(f'Created {target} with reviewed Serbian Cyrillic resources.')
