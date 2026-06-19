# fallback scan with pypdf — detects XFA + JS actions
import pypdf, glob

xfa = js = acro_only = no_form = errors = 0
xfa_files = []
for p in glob.glob("*.pdf"):
    try:
        r = pypdf.PdfReader(p)
        root = r.trailer["/Root"]
        acro = root.get("/AcroForm")
        if not acro:
            no_form += 1; continue
        acro = acro.get_object()
        has_xfa = "/XFA" in acro
        # crude JS detection: names tree or doc-level JavaScript
        has_js = "/JavaScript" in root.get("/Names", {}) if root.get("/Names") else False
        if has_xfa: xfa += 1; xfa_files.append(p)
        if has_js: js += 1
        if not has_xfa and not has_js: acro_only += 1
    except Exception:
        errors += 1

print(f"XFA (likely logic): {xfa}")
print(f"has JS: {js}")
print(f"AcroForm-only (no logic): {acro_only}")
print(f"no form fields: {no_form}")
print(f"errors: {errors}")
for f in xfa_files[:30]: print("  XFA:", f)
