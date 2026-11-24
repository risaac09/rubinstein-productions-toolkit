"""
The light axis of the per-shot scene model: one `light` block built from a
measure report (rpresolve/measure.py `measure`, or its saved --json).

Pure and stdlib only: every number here was computed by measure.py
(`pooled_lighting`, `skin_stats` `L_srgb`, `shadow_classes`, `transfer_of`,
`luma_stats` p50). This module renames and bands them; it derives no maths.
A missing number is null and `withheld` says why, never zero.
"""

# House defaults, not standards: a person sets these. A shot whose median luma
# (luma p50, IRE on the 2.4 display reading) is at or over KEY_HIGH_P50_IRE is
# high key, at or under KEY_LOW_P50_IRE low key, otherwise mid.
KEY_HIGH_P50_IRE = 60.0
KEY_LOW_P50_IRE = 30.0

FIELDS = ("ratio_stops", "ratio", "lit_side", "key", "skin_l_srgb",
          "shadow_readable_pct", "shadow_lost_pct")

# What in the block is a person's call. The other fields are measurements; whether
# a measured ratio or side suits the scene is still taste, asked, never scored.
TASTE = {
    "key": "the band thresholds are house defaults (KEY_HIGH_P50_IRE, KEY_LOW_P50_IRE); a person sets them",
    "ratio": "measured; flat or dramatic for this scene is a person's call",
    "lit_side": "measured; whether the light is motivated is a person's call",
}
REVIEW_QUESTION = "Is this shot's key and ratio the look the scene wants?"


def key_of(p50):
    """'high', 'mid' or 'low' from luma p50 in IRE, on the house thresholds.
    shortcut: measure's p50 counts letterbox and pillarbox bars, so a boxed shot
    reads darker; band on bar-free luma if boxed shots get keyed."""
    if p50 >= KEY_HIGH_P50_IRE:
        return "high"
    if p50 <= KEY_LOW_P50_IRE:
        return "low"
    return "mid"


def light_block(report):
    """The `light` block of one shot from its measure report."""
    block = dict.fromkeys(FIELDS)
    reasons = []
    transfer = (report.get("transfer") or {}).get("withheld")
    if transfer:
        # A log or HDR curve read as display Rec.709 lifts every luma: no key, no ratio.
        block["withheld"] = f"tagged {transfer}, not display Rec.709: every light number withheld"
        return block
    p50 = (report.get("luma") or {}).get("p50")
    if p50 is None:
        reasons.append("key: no luma p50 in the report")
    else:
        block["key"] = key_of(p50)
    lit = report.get("lighting")
    if isinstance(lit, dict):
        block.update(ratio_stops=lit["stops"], ratio=lit["ratio"], lit_side=lit["lit_side"])
    else:
        reasons.append(f"ratio: {lit or 'not in the report'}")
    sh = report.get("shadows")
    if isinstance(sh, dict):
        block.update(shadow_readable_pct=sh["readable_pct"], shadow_lost_pct=sh["lost_pct"])
    else:
        reasons.append(f"shadow: {sh or 'not in the report'}")
    skin = report.get("skin")
    clipped = [w for w in (skin.get("warnings") or []) if "at clip" in w] if isinstance(skin, dict) else []
    if clipped:
        # measure.skin_sample: on a clipped cheek L* is a limit, not a reading.
        reasons.append(f"skin: {clipped[0]}")
    elif isinstance(skin, dict):
        block["skin_l_srgb"] = skin["L_srgb"]
    else:
        reasons.append(f"skin: {skin or 'not in the report'}")
    block["withheld"] = "; ".join(reasons) or None
    return block


def shot_record(report):
    """{file, light, taste, review}: one shot's light block, with which of its
    fields are taste and the question put to a person."""
    return {"file": report.get("file"), "light": light_block(report),
            "taste": TASTE, "review": REVIEW_QUESTION}


def format_line(record):
    """One line per shot for the terminal."""
    li = record["light"]

    def show(v, unit=""):
        return "null" if v is None else f"{v}{unit}"

    line = (f"{record['file']}: key {show(li['key'])}, ratio {show(li['ratio'], ':1')} "
            f"({show(li['ratio_stops'])} stops, {show(li['lit_side'])} lit), "
            f"skin L* {show(li['skin_l_srgb'])}, shadow {show(li['shadow_readable_pct'], '%')} readable "
            f"/ {show(li['shadow_lost_pct'], '%')} lost")
    return line + (f"\n  withheld: {li['withheld']}" if li["withheld"] else "")
