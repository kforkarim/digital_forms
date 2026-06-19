"""Extract executable logic from digital (XFA/JS) forms -> canonical relationships.

The logic-prevalence scan over the digital_forms corpus found ~14 XFA + ~51 JS
forms out of ~675. Most of that logic is CALCULATION (sum/total/validate), not
conditional VISIBILITY — but the visibility rules that DO exist are real,
machine-readable dependency ground truth, which the project otherwise has zero
of (audit #9). This script mines that subset.

Two sources of executable logic in a fillable PDF:
  1. XFA (Adobe LiveCycle) — an XML packet under /AcroForm/XFA. Field <field>
     nodes can carry <event activity="..."><script> with presence logic
     ("this.presence = relevant ? 'visible' : 'hidden'") and bind/calculate.
  2. AcroForm field-level JavaScript — /AA (additional actions) with /C
     (calculate) or a field /A action; doc-level /Names/JavaScript.

We classify each rule:
  * conditional_visibility  -> a real DEPENDENCY (gold for the dependency task)
  * calculation             -> not a dependency (recorded separately; useful as
                               domain signal but NOT dependency ground truth)
  * validation/format       -> not a dependency

Output: per-form JSON with canonical Relationship dicts + a classification
summary, plus a corpus-level manifest. The conditional_visibility relationships
become the V3.1 validation set: score the model's suggested dependencies against
these on the same forms.

Usage:
  python scripts/digital_logic_extract.py --in digital_forms --out gold_digital
  # then validate a model's output against the gold:
  python scripts/digital_logic_extract.py --validate gold_digital/manifest.json \
      --against /tmp/v31_clean/teacher
"""

import argparse
import glob
import json
import os
import re
import sys

# canonical schema (direction + operator preserved)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
try:
    from app.services import relationship_schema as rs
except Exception:
    rs = None  # validation-only mode doesn't need it


# ----------------------------- XFA parsing -----------------------------
# presence-toggle patterns commonly emitted by LiveCycle Designer:
#   this.presence = (Field.rawValue == "Yes") ? "visible" : "hidden";
#   if (x.rawValue == "1") this.presence = "visible"; else this.presence = "hidden";
_PRESENCE_RE = re.compile(
    r"""(?P<target>\w[\w.\[\]]*)?\.?presence\s*=\s*  # this.presence =
        .*?(?P<src>\b[\w]+)\.rawValue\s*             # SomeField.rawValue
        (?P<op>==|!=|===|!==)\s*                      # comparison
        ["'](?P<val>[^"']+)["']                       # "Yes"
        .*?["'](?P<then>visible|hidden|invisible)["']  # first presence keyword
    """,
    re.S | re.X | re.I,
)

_CALC_RE = re.compile(r"\.rawValue\s*=\s*.*?(sum|\+|\*|total|\bRound\b|Math\.)", re.I)


def _xfa_xml(pdf):
    """Return concatenated XFA script text, or '' if none."""
    try:
        acro = pdf.Root.get("/AcroForm")
        if not acro:
            return ""
        xfa = acro.get("/XFA")
        if xfa is None:
            return ""
        chunks = []
        # XFA may be an array of [name, stream, name, stream, ...] or one stream
        items = list(xfa) if hasattr(xfa, "__iter__") and not hasattr(xfa, "read_bytes") else [xfa]
        for it in items:
            try:
                data = it.read_bytes() if hasattr(it, "read_bytes") else None
                if data:
                    chunks.append(data.decode("utf-8", "ignore"))
            except Exception:
                continue
        return "\n".join(chunks)
    except Exception:
        return ""


def _norm_op(op, then_keyword):
    """Map JS comparison + presence keyword to a canonical operator on the parent
    value. If the 'then' branch hides on a positive match we invert."""
    eq = op in ("==", "===")
    visible_on_match = then_keyword.lower() == "visible"
    # field is visible when (src OP val) matches AND then=visible, i.e. equals.
    if eq and visible_on_match:
        return "equals"
    if eq and not visible_on_match:
        return "not_equals"
    if (not eq) and visible_on_match:
        return "not_equals"
    return "equals"


def extract_form(path):
    import pikepdf
    rels, calc_count, val_count = [], 0, 0
    try:
        pdf = pikepdf.open(path)
    except Exception as e:
        return {"form": os.path.basename(path), "error": str(e),
                "dependencies": [], "calculations": 0}
    xml = _xfa_xml(pdf)
    if xml:
        for m in _PRESENCE_RE.finditer(xml):
            src = m.group("src")
            val = m.group("val")
            op = _norm_op(m.group("op"), m.group("then"))
            target = m.group("target") or ""
            # target field name: the field whose <field> node this script sits in
            # is the dependent (child). We approximate child by the nearest field
            # name above the script; if not resolvable, record src only.
            child = _nearest_field_name(xml, m.start()) or target or "(unknown_child)"
            if child == "(unknown_child)" or child == src:
                continue
            if rs is not None:
                rel = rs.dependency(parent=src, child=child, operator=op, value=val,
                                    source="import:xfa", observability="explicit",
                                    imported_ground_truth=True)
                rels.append(rel.to_dict())
        calc_count += len(_CALC_RE.findall(xml))
    pdf.close()
    return {"form": os.path.basename(path),
            "dependencies": [r for r in rels],
            "n_dependencies": len(rels),
            "calculations": calc_count}


def _nearest_field_name(xml, pos):
    """Find the name="..." of the <field> enclosing position pos (best-effort)."""
    head = xml[:pos]
    # last <field name="X"
    m = None
    for m in re.finditer(r'<field\b[^>]*\bname="([^"]+)"', head):
        pass
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", default="")
    ap.add_argument("--out", dest="outdir", default="gold_digital")
    ap.add_argument("--validate", default="", help="manifest.json to score against")
    ap.add_argument("--against", default="", help="teacher dir of model output")
    args = ap.parse_args()

    if args.validate:
        return _validate(args.validate, args.against)

    os.makedirs(os.path.join(args.outdir, "forms"), exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.indir, "*.pdf")))
    manifest = {"forms": {}, "totals": {"forms": 0, "dependency_forms": 0,
                                        "dependencies": 0, "calc_forms": 0}}
    for p in files:
        res = extract_form(p)
        manifest["totals"]["forms"] += 1
        if res.get("n_dependencies"):
            manifest["totals"]["dependency_forms"] += 1
            manifest["totals"]["dependencies"] += res["n_dependencies"]
            json.dump(res, open(os.path.join(args.outdir, "forms",
                      os.path.basename(p) + ".json"), "w"), indent=1)
            manifest["forms"][os.path.basename(p)] = {
                "n_dependencies": res["n_dependencies"],
                "dependencies": res["dependencies"]}
        if res.get("calculations"):
            manifest["totals"]["calc_forms"] += 1
    json.dump(manifest, open(os.path.join(args.outdir, "manifest.json"), "w"), indent=1)
    t = manifest["totals"]
    print(f"scanned {t['forms']} forms")
    print(f"  forms with conditional-visibility dependencies: {t['dependency_forms']}")
    print(f"  total gold dependencies (visibility): {t['dependencies']}")
    print(f"  forms with calculation logic (NOT dependencies): {t['calc_forms']}")
    print(f"\ngold dependency set written to {args.outdir}/ "
          f"({t['dependency_forms']} forms) — use to validate model output")


def _validate(manifest_path, against_dir):
    """Score a model's suggested dependencies against the imported gold."""
    gold = json.load(open(manifest_path))
    gforms = gold.get("forms", {})
    if not against_dir:
        print("provide --against <teacher dir> to score")
        print(f"gold has {len(gforms)} forms with "
              f"{gold['totals']['dependencies']} dependencies")
        return
    # match by form-name substring; compare (parent,child) pairs directionally
    import glob as _g
    model = {}
    for p in _g.glob(os.path.join(against_dir, "*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        name = (d.get("form_name") or "")
        deps = set()
        for k, v in (d.get("edge_meta") or {}).items():
            if v.get("rel") == "parent_dependent":
                a, b = k.split("_")
                deps.add((a, b))
        model[name] = deps
    print("validation is name-keyed; gold uses field NAMES, model uses indices —")
    print("this scorer reports COUNTS for manual alignment, not auto-matched F1,")
    print("because gold field names and flattened detection indices differ.")
    print(f"gold forms: {len(gforms)}  model forms: {len(model)}")


if __name__ == "__main__":
    main()
