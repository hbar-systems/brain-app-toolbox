#!/usr/bin/env python3
"""check.py — lint a brain-app before commit (kit 0.1.0, 2026-10-22).

    python3 kit/check.py [app_dir]

Validates brain-app.yaml against the brain's manifest schema (description <= 240 characters, tab
label <= 24, known permissions; an over-long field makes the brain's Update answer 422 and look
like a silent no-op), then checks the sandbox rules: no <form>, no type=submit, no native <select>,
no window.open / alert / confirm / prompt, no fetch to /api/bf or /cc, the kit vendored.
Exit 0 when clean.
"""
import json, pathlib, re, sys

SCHEMA_CANDIDATES = [
    pathlib.Path(__file__).resolve().parents[2] / "brainfoundry-nous/api/schemas/brain-app.schema.json",
    pathlib.Path(__file__).resolve().parents[3] / "brainfoundry-nous/api/schemas/brain-app.schema.json",
    pathlib.Path("/home/hands/world/systems/brainfoundry/repos/brainfoundry-nous/api/schemas/brain-app.schema.json"),
]
RULES = [
    (r"<form\b", "a <form> (the sandbox has no allow-forms; drive actions with onClick)"),
    (r"type\s*=\s*[\"']?submit", "type=submit (does nothing in the sandbox)"),
    (r"<select\b", "a native <select> (use BF.select or BF.chips)"),
    (r"window\.open\s*\(|\balert\s*\(|\bconfirm\s*\(|\bprompt\s*\(", "popups or modal dialogs (blocked in the sandbox)"),
    (r"fetch\(\s*[`'\"]/(api/bf|cc)/", "a fetch to the console (refused for app frames; use BF.ask)"),
    (r"innerHTML\s*=\s*[^'\"`;]*\b(text|result|reply|content|body)\b", "innerHTML from brain or web content (use BF.md / BF.lines)"),
]


def main(app_dir: str) -> int:
    import yaml, jsonschema
    d = pathlib.Path(app_dir).resolve()
    bad = []
    try:
        man = yaml.safe_load((d / "brain-app.yaml").read_text())
    except yaml.YAMLError as e:
        print(f"FAIL brain-app.yaml does not parse (quote a value that holds a colon): {str(e).splitlines()[0]}")
        return 1
    if not isinstance(man, dict):
        print("FAIL brain-app.yaml is not a mapping")
        return 1
    schema_p = next((p for p in SCHEMA_CANDIDATES if p.is_file()), None)
    if not schema_p:
        bad.append("schema not found (expected brainfoundry-nous beside this repo)")
    else:
        v = jsonschema.Draft7Validator(json.loads(schema_p.read_text()))
        for e in v.iter_errors(man):
            bad.append(f"manifest: {'/'.join(map(str, e.path)) or '(root)'}: {e.message[:160]}")
    bundle = d / str((man.get("entries") or {}).get("ui_bundle") or "./")
    if (man.get("entries") or {}).get("ui_bundle"):
        if not (bundle / "index.html").is_file():
            bad.append(f"no index.html in the ui bundle {bundle}")
        for k in ("kit/bf-kit.js", "kit/bf-kit.css"):
            if not (bundle / k).is_file():
                bad.append(f"kit not vendored: {k}")
    for f in sorted(list(d.rglob("*.html")) + list(d.rglob("*.js"))):
        if "/kit/" in str(f) or "node_modules" in str(f) or "/.git/" in str(f):
            continue
        s = f.read_text(errors="replace")
        for pat, why in RULES:
            for m in re.finditer(pat, s, re.I):
                line = s.count("\n", 0, m.start()) + 1
                bad.append(f"{f.relative_to(d)}:{line}: {why}")
    for b in bad:
        print("FAIL", b)
    print("clean" if not bad else f"{len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
