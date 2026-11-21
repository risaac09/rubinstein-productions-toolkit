"""One structured caption per shot keyframe from Qwen2.5-VL via Ollama on loopback."""
import base64
import json
import urllib.request

from . import config
from .store import clean_tags

OLLAMA = config.get("ollama_url")
MODEL = config.get("vlm")
# lens_feel and camera_move left the prompt: "normal" in 99.8 percent and "static" in 94 percent of over 20,000 captions.
# Existing captions keep them; nothing reads them.
FIELDS = ["subject", "setting", "action", "shot_size", "time_of_day", "light", "tags"]
SCHEMA = {"type": "object", "required": FIELDS, "properties": {
    **{k: {"type": "string"} for k in FIELDS if k != "tags"},
    "tags": {"type": "array", "items": {"type": "string"}}}}
PROMPT = ("You are logging footage for an editor. Describe this video frame as JSON with: subject (who or what), "
          "setting (place), action (what is happening), shot_size (extreme wide, wide, medium, close-up, extreme close-up), "
          "time_of_day, light (for example overcast, golden hour, hard sun, tungsten, mixed), and tags (5 to 12 short "
          "lowercase nouns or adjectives an editor would search for). Describe only what is visible. "
          "Do not name real people.")
# On film scans the model guessed a scene ("football players" for a lawn, "explosion" for clouds). In a 20-shot A/B this
# hint fixed both without a new error; its last sentence keeps texture words from crowding content out of the tags.
FILM_HINT = (" This frame is a scan of old home-movie film (8mm or 16mm): expect grain, faded or shifted colour, blur and "
             "scratches. Describe only what is clearly visible. When the scene is unclear, say so (for example \"unclear "
             "outdoor scene\") rather than guessing a specific place, object or activity. Keep grain, fading, blur and "
             "scratches out of the tags.")


def caption(thumb, film=False):
    # no motion hint: it set off tag loops ("ear, ear, ear")
    # num_predict ends a loop in seconds; a full caption is ~150 tokens
    body = {"model": MODEL, "stream": False, "format": SCHEMA, "options": {"temperature": 0, "num_predict": 400},
            "messages": [{"role": "user", "content": PROMPT + (FILM_HINT if film else ""),
                          "images": [base64.b64encode(open(thumb, "rb").read()).decode()]}]}
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(json.loads(r.read())["message"]["content"])
    out["tags"] = clean_tags(out.get("tags"))
    return out
