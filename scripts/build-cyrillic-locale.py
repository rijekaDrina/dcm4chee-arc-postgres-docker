#!/usr/bin/env python3
"""Add a Serbian Cyrillic locale alongside the bundled Serbian Latin locale."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import zipfile

root = Path(__file__).resolve().parents[1]
source = root / "build/archive-ui.war"
target = root / "build/archive-ui-cyrillic.war"
if not source.is_file():
    raise SystemExit("Missing build/archive-ui.war. Copy it from the running 5.35.1 archive container first.")

mapping = str.maketrans({
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "đ": "ђ", "e": "е",
    "ž": "ж", "z": "з", "i": "и", "j": "ј", "k": "к", "l": "л", "m": "м",
    "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т", "ć": "ћ",
    "u": "у", "f": "ф", "h": "х", "c": "ц", "č": "ч", "š": "ш",
    "A": "А", "B": "Б", "V": "В", "G": "Г", "D": "Д", "Đ": "Ђ", "E": "Е",
    "Ž": "Ж", "Z": "З", "I": "И", "J": "Ј", "K": "К", "L": "Л", "M": "М",
    "N": "Н", "O": "О", "P": "П", "R": "Р", "S": "С", "T": "Т", "Ć": "Ћ",
    "U": "У", "F": "Ф", "H": "Х", "C": "Ц", "Č": "Ч", "Š": "Ш",
})
protected = re.compile(
    r"\{\$[^}]+\}|<[^>]+>|\b(?:DICOM|DCM4CHEE|DCM4CHE|HL7|FHIR|RESTful|REST|HTTP|HTTPS|LDAP|OIDC|"
    r"JSON|XML|URL|URI|SOP|UID|AE|AET|PDF|CSV|UTF-8|UI|ID|CT|MR|US|CR|DX|XA|MG|"
    r"Keycloak|WildFly|Dcm4chee|dcm4chee|dcm4che)\b"
)

def cyrillic(text: str) -> str:
    tokens: list[str] = []
    def hold(match):
        tokens.append(match.group(0))
        return f"\u0000{len(tokens) - 1}\u0000"
    text = protected.sub(hold, text)
    for old, new in (("dž", "џ"), ("Dž", "Џ"), ("DŽ", "Џ"),
                     ("lj", "љ"), ("Lj", "Љ"), ("LJ", "Љ"),
                     ("nj", "њ"), ("Nj", "Њ"), ("NJ", "Њ")):
        text = text.replace(old, new)
    text = text.translate(mapping)
    for index, token in enumerate(tokens):
        text = text.replace(f"\u0000{index}\u0000", token)
    return text

def transform_json(payload: bytes, translate_all: bool = False) -> bytes:
    obj = json.loads(payload)
    if translate_all and isinstance(obj, dict) and isinstance(obj.get("translations"), dict):
        obj["locale"] = "sr-Cyrl"
        obj["translations"] = {
            key: cyrillic(value) if isinstance(value, str) else value
            for key, value in obj["translations"].items()
        }
    elif isinstance(obj, dict):
        def visit(value, parent_key=""):
            if isinstance(value, dict):
                return {key: (cyrillic(item) if key in ("title", "description") and isinstance(item, str)
                              else visit(item, key)) for key, item in value.items()}
            if isinstance(value, list):
                return [visit(item, parent_key) for item in value]
            return value
        obj = visit(obj)
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


with zipfile.ZipFile(source) as original:
    sr_translation = json.loads(original.read("sr.json"))
sr_translation["locale"] = "sr-Cyrl"
sr_translation["translations"] = {
    key: cyrillic(value) if isinstance(value, str) else value
    for key, value in sr_translation["translations"].items()
}
translation_bytes = (json.dumps(sr_translation, ensure_ascii=False, indent=2) + "\n").encode()
(root / "build/sr-Cyrl.json").write_bytes(translation_bytes)
if "--prepare" in sys.argv:
    print("Prepared build/sr-Cyrl.json, preserving Angular placeholders and technical identifiers.")
    raise SystemExit(0)
compiled = root / "build/ui-source/target/webapp/sr-Cyrl"
if not (compiled / "index.html").is_file():
    raise SystemExit("Missing compiled Cyrillic UI. Run python3 scripts/compile-cyrillic-ui.py first.")

if not (root / "build/ui-source/target/classes/org/dcm4chee/arc/ui2/UrlRewriting.class").is_file():
    raise SystemExit("Missing UI routing classes. Run python3 scripts/compile-ui-routing.py first.")

def flags_payload(data: bytes) -> bytes:
    flags = json.loads(data)
    latin = flags.get("sr")
    if latin:
        latin.update(name="Serbian Latin", nativeName="Srpski (latinica)")
        flags["sr-Cyrl"] = {
            **latin, "name": "Serbian Cyrillic", "nativeName": "Српски (ћирилица)",
            "code": "sr-Cyrl", "countryCode": "rs",
        }
    return (json.dumps(flags, ensure_ascii=False, indent=2) + "\n").encode()

with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
    for info in original.infolist():
        name = info.filename
        if name.startswith("WEB-INF/classes/org/dcm4chee/arc/ui2/UrlRewriting"):
            continue
        if info.is_dir():
            output.writestr(name, b"")
            continue
        data = original.read(name)
        if re.search(r"/main-[^/]+\.js$", name):
            text = data.decode("utf-8")
            text = text.replace(r"dcm4chee-arc\/ui2\/(\w{2})\//gm", r"dcm4chee-arc\/ui2\/([\w-]+)\//gm")
            text = text.replace(r"/\/ui2\/\w{2}/", r"/\/ui2\/[\w-]+/")
            data = text.encode()
        if name.endswith("assets/locale/languageCodeBase64Flag.json"):
            data = flags_payload(data)
        output.writestr(name, data)
        if name == "sr.json":
            output.writestr("sr-Cyrl.json", translation_bytes)
        elif name.startswith("sr/assets/schema/") and name.endswith(".json"):
            output.writestr("sr-Cyrl/" + name[3:], transform_json(data))
    for path in (root / "build/ui-source/target/classes").rglob("UrlRewriting*.class"):
        output.writestr("WEB-INF/classes/" + path.relative_to(root / "build/ui-source/target/classes").as_posix(), path.read_bytes())
    # Angular must compile the Cyrillic messages into the bundles. Copying the
    # Latin JS files into a new locale directory would still render Latin text.
    for path in sorted(compiled.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(compiled).as_posix()
        if relative.startswith("assets/schema/"):
            continue  # Use the Serbian schemas transliterated above.
        data = path.read_bytes()
        if relative.endswith("assets/locale/languageCodeBase64Flag.json"):
            data = flags_payload(data)
        output.writestr("sr-Cyrl/" + relative, data)
print(f"Created {target} with compiled Serbian Cyrillic UI and Serbian Latin resources.")
