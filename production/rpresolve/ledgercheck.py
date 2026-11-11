"""
ledgercheck: the draft farm's ledger, checked at build. The draft farm (drafts.py) writes
into drafts.json, per draft, an `effects` ledger of the manifest's effects as drawn,
approximated or not_drawn, each with its span indices. The rule: the build applies only
what the draft drew. apply_effect and assemble_timeline take that file (`ledger`, with
`draft` naming the picked plan when the file holds several) and an `override` list of
effect names. A dry run flags every effect the draft marked not_drawn; a real run refuses
unless each flagged effect is named in override, so a skipped effect is a decision. With no
ledger, nothing is checked and the report says so.
"""

import json
import os

from rpresolve.workflows import Refused

BUCKETS = ("drawn", "approximated", "not_drawn")
NONE_GIVEN = "no ledger given: nothing was checked against the draft"


def load(path, draft=None):
    """The picked draft's ledger from a drafts.json: {path, draft, effects}."""
    if not os.path.isabs(path):
        raise Refused("ledger must be an absolute path")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise Refused(f"cannot read the ledger: {e}")
    rows = [d for d in (doc.get("drafts") if isinstance(doc, dict) else None) or []
            if isinstance(d, dict)]
    if not rows:
        raise Refused("the ledger is not a drafts.json: it holds no drafts")
    if draft is None and len(rows) > 1:
        raise Refused(f"the ledger holds {len(rows)} drafts ({', '.join(str(d.get('plan')) for d in rows)}); "
                      "name the picked one as draft")
    pick = rows[0] if draft is None else next((d for d in rows if d.get("plan") == draft), None)
    if pick is None:
        raise Refused(f"the ledger has no draft '{draft}'")
    led = pick.get("effects")
    if not isinstance(led, dict) or not all(isinstance(led.get(k), list) for k in BUCKETS):
        raise Refused(f"draft '{pick.get('plan')}' has no effects ledger")
    return {"path": path, "draft": pick.get("plan"), "effects": led}


def pairs(effects):
    """Every (effect, span) a ledger lists, in any bucket."""
    return {(r["effect"], s) for k in BUCKETS for r in effects[k] for s in r["spans"]}


def check(ledger, wanted, override=None):
    """The report for the (effect, span) pairs a build would apply: {checked, flagged
    [{effect, spans, overridden}], note, ...}. `ledger` is load()'s result or None."""
    if ledger is None:
        return {"checked": False, "flagged": [], "note": NONE_GIVEN}
    skipped = {(r["effect"], s) for r in ledger["effects"]["not_drawn"] for s in r["spans"]}
    hit, ok = sorted(set(wanted) & skipped), set(override or [])
    flagged = [{"effect": e, "spans": sorted(s for x, s in hit if x == e), "overridden": e in ok}
               for e in sorted({x for x, _ in hit})]
    return {"checked": True, "path": ledger["path"], "draft": ledger["draft"],
            "flagged": flagged, "override": sorted(ok),
            "note": ("the draft did not draw: " + "; ".join(
                f"{f['effect']} on span(s) {f['spans']}" for f in flagged)) if flagged
            else "the draft drew everything this build applies"}


def must_match(ledger, manifest):
    """Refuse a ledger that is not this manifest's: its (effect, span) pairs differ from
    those the draft farm would write for the manifest (a path or the dict, already
    validated by assemble's plan). Returns the pairs."""
    from rpresolve import drafts
    if isinstance(manifest, str):
        with open(manifest, encoding="utf-8") as f:
            manifest = json.load(f)
    want = pairs(drafts.ledger(manifest))
    if ledger is not None and pairs(ledger["effects"]) != want:
        raise Refused(f"the ledger (draft '{ledger['draft']}') is not this manifest's: its effects "
                      "and spans differ from the manifest's; pass the drafts.json and draft of "
                      "the plan this manifest came from")
    return want


def refuse_unless_overridden(report):
    """The real run's gate: Refused while a flagged effect is not named in override."""
    open_ = [f for f in report["flagged"] if not f["overridden"]]
    if open_:
        raise Refused("the draft did not draw " + "; ".join(
            f"{f['effect']} on span(s) {f['spans']}" for f in open_)
            + ". The build applies only what the draft drew: drop it, or name it in override "
            "to apply it anyway.")


def summary(report):
    """A short clause for a tool summary."""
    if not report["checked"]:
        return "; " + report["note"]
    held = [f["effect"] for f in report["flagged"] if not f["overridden"]]
    if held:
        return "; FLAGGED, the draft did not draw " + ", ".join(held)
    return ("; the draft did not draw " + ", ".join(f["effect"] for f in report["flagged"])
            + " (overridden)") if report["flagged"] else "; ledger checked: the draft drew all of it"
