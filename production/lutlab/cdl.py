"""lutlab.cdl: the ASC CDL (slope, offset, power per channel, one saturation) as a dict, and its .cc file.

Same shape as lutlab.shotmatch's CDL (held PR); once that merges it can import these."""

import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722])
IDENTITY = {"slope": np.ones(3), "offset": np.zeros(3), "power": np.ones(3), "sat": 1.0}


def cdl_apply(rgb, c):
    """ASC CDL: slope, offset, power per channel, then saturation about Rec.709 luma."""
    x = np.maximum(np.asarray(rgb, float) * c["slope"] + c["offset"], 0) ** c["power"]
    y = (x @ LUMA)[..., None]
    return y + c["sat"] * (x - y)


def cdl_xml(cdl, name):
    """ASC CDL 1.01 ColorCorrection XML, for a Resolve CDL import."""
    f = lambda v: " ".join(f"{x:.6f}" for x in v)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<ColorCorrection xmlns="urn:ASC:CDL:v1.01" id=' + quoteattr(name) + '>\n'
            f'  <SOPNode>\n    <Slope>{f(cdl["slope"])}</Slope>\n    <Offset>{f(cdl["offset"])}</Offset>\n'
            f'    <Power>{f(cdl["power"])}</Power>\n  </SOPNode>\n  <SATNode>\n    <Saturation>{cdl["sat"]:.6f}</Saturation>\n'
            '  </SATNode>\n</ColorCorrection>\n')


def read_cdl(path):
    """The CDL dict of a .cc file (ASC CDL 1.01 ColorCorrection)."""
    ns = {"c": "urn:ASC:CDL:v1.01"}
    root = ET.parse(path).getroot()
    get = lambda tag: root.find(f".//c:{tag}", ns).text.split()
    return {"slope": np.array(get("Slope"), float), "offset": np.array(get("Offset"), float),
            "power": np.array(get("Power"), float), "sat": float(get("Saturation")[0])}
