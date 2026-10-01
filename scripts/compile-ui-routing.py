#!/usr/bin/env python3
"""Extend the UI servlet routing to support sr-Cyrl deep links."""
import re,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=root/'build/ui-source'
p=source/'src/main/java/org/dcm4chee/arc/ui2/UrlRewriting.java'
s=p.read_text()
if '"/sr-Cyrl/study/*"' not in s:
    patterns=re.findall(r'"(/sr/[^"\n]+)"',s)
    s=s.replace('\n})\npublic class',',\n'+',\n'.join('        "'+v.replace('/sr/','/sr-Cyrl/')+'"' for v in patterns)+'\n})\npublic class')
s=s.replace('substring(0, 3)', "substring(0, httpServletRequest.getServletPath().indexOf('/', 1))")
p.write_text(s)
subprocess.run(['docker','run','--rm','--entrypoint','sh','-v',str(source)+':/app:Z','dcm4che/dcm4chee-arc-psql:5.35.1-secure','-c','javac --release 17 -cp /opt/wildfly/modules/system/layers/base/jakarta/servlet/api/main/jakarta.servlet-api-6.0.0.jar -d /app/target/classes /app/src/main/java/org/dcm4chee/arc/ui2/UrlRewriting.java'],check=True)
