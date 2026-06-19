import pikepdf, glob, sys

xfa = js_actions = acro_only = no_form = errors = 0
xfa_files = []
for p in glob.glob("digital_forms/*.pdf"):
    try:
        pdf = pikepdf.open(p)
        acro = pdf.Root.get("/AcroForm", None)
        if acro is None:
            no_form += 1; continue
        has_xfa = "/XFA" in acro
        # scan all objects for field-level JS / calculate / validate actions
        raw = b""
        has_js = False
        for obj in pdf.objects:
            try:
                if "/AA" in obj or "/JS" in obj:   # additional-actions / JS
                    has_js = True; break
            except Exception:
                pass
        if has_xfa:
            xfa += 1; xfa_files.append(p)
        if has_js:
            js_actions += 1
        if not has_xfa and not has_js:
            acro_only += 1
        pdf.close()
    except Exception:
        errors += 1

total = xfa + acro_only + no_form + errors
print(f"total scanned: {total}")
print(f"  XFA (likely logic):        {xfa}")
print(f"  has JS/calc actions:       {js_actions}")
print(f"  AcroForm-only (no logic):  {acro_only}")
print(f"  no form fields at all:     {no_form}")
print(f"  errors:                    {errors}")
print("\nXFA files (the candidates worth mining):")
for f in xfa_files[:30]:
    print("  ", f)
