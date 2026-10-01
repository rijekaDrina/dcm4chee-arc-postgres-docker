#!/usr/bin/env python3
"""Make Serbian Latin the fallback for users without a saved UI language.

The pinned upstream UI defaults to English in both its welcome servlet and
Angular bundle. Patch only those two exact fallback expressions, preserving
explicit user choices and all other locale routes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path


def patch(war_path: Path) -> int:
    servlet = b"const lang=localStorage.getItem('current_language')||'en';"
    servlet_sr = b"const lang=localStorage.getItem('current_language')||'sr';"
    angular = (b'this.setCurrentLanguageBasedOnCode("en"),'
               b'window.location.href="/dcm4chee-arc/ui2/en/"')
    angular_sr = (b'this.setCurrentLanguageBasedOnCode("sr"),'
                  b'window.location.href="/dcm4chee-arc/ui2/sr/"')
    fd, temporary = tempfile.mkstemp(prefix=war_path.name + '.', suffix='.tmp', dir=war_path.parent)
    os.close(fd)
    changed = 0
    try:
        with zipfile.ZipFile(war_path) as source, zipfile.ZipFile(temporary, 'w') as target:
            for item in source.infolist():
                content = source.read(item.filename)
                if item.filename.endswith('SelectLangServlet.class'):
                    if servlet_sr not in content:
                        if content.count(servlet) != 1:
                            raise RuntimeError('Expected welcome servlet fallback was not found exactly once')
                        content = content.replace(servlet, servlet_sr)
                        changed += 1
                elif '/main-' in item.filename and item.filename.endswith('.js'):
                    if angular_sr not in content:
                        if content.count(angular) != 1:
                            raise RuntimeError(f'Expected Angular fallback was not found exactly once: {item.filename}')
                        content = content.replace(angular, angular_sr)
                        changed += 1
                target.writestr(item, content)
        if changed:
            os.chmod(temporary, war_path.stat().st_mode & 0o777)
            os.replace(temporary, war_path)
        else:
            os.unlink(temporary)
        return changed
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    path = Path(sys.argv[1]) if len(sys.argv) == 2 else Path('build/archive-ui.war')
    print(f'Updated Serbian Latin fallback in {patch(path)} WAR entries: {path}')
