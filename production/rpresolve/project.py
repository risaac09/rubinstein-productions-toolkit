"""
rpresolve.project: the chain that starts a project from a camera card, resumable.

State lives in `<work root>/<slug>/project.json`: the steps with their status, the card
folders by path, and what is still open. Every step is an existing command run as a
subprocess (`offload.sh`, `card`, `detect`, `sheets`, an indexer command from the config);
nothing is reimplemented. A step that writes does so only with apply; a dry run runs the
read-only commands and says what the rest would do. The chain stops at the first step that
needs a person and says what it needs in one line; running it again resumes.
"""

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from . import card as rpcard
from . import paths as rppaths

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTION = os.path.dirname(HERE)
OFFLOAD = os.path.join(PRODUCTION, "offload.sh")
STATE = "project.json"
CONFIG_ENV = "RP_PIPELINE_CONFIG"
CONFIG_KEYS = ("landing_root", "work_root", "indexer", "offsite_remote", "sheets_root", "music_root")
READ_ELSEWHERE = ("footage_index",)  # the indexer's own block; project does not use it but must not refuse it
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

PER_CARD = ("identify", "index", "sheets")  # run once for every card of the project
STEPS = (("copy", "copy and verify"), ("identify", "identify camera, profile, lens"),
         ("ask", "open questions"), ("index", "index the card"),
         ("sheets", "whole-card contact sheets"), ("resolve", "Resolve project colour"),
         ("shotlist", "shot list, cut times, drafts, watch, pick"),
         ("build", "build the timeline"), ("deliver", "queue, deliver check, offsite"))

BUILD_NEXT = "project build SLUG [--piece NAME] [--manifest MANIFEST] [--name NAME] --apply"
# edit-methods.md, "The job, footage first": the command each hand step runs next.
NEXT = {
    "shotlist": "write the shot list, then: notes measure AUDIO --out notes.json; notes snap; "
                "shotlist compile LIST --cuts plan.json --out PLAN_DIR; drafts PLAN_DIR --out DIR; "
                "watch sheets DRAFT --cuts plan.json --out SHEETS",
    "build": "project answer SLUG [--piece NAME] --shape WxH --look LUT --destination KEY --name-part k=v ... "
             "--pick MANIFEST [--ledger DRAFTS] [--exclude LIST] --apply (once), then " + BUILD_NEXT,
    "deliver": "a person starts the render, then deliver_check on it, then offload.sh safe CARD REMOTE",
}
ANSWERS = (("shape", "delivery shape", "--shape WxH"), ("look", "look", "--look LUT"),
           ("destination", "destination", "--destination KEY --name-part client=X --name-part slug=Y"))


class ProjectError(ValueError):
    """A refusal; the message is one line."""


def run_cmd(cmd, cwd=None, env=None):
    """(returncode, stdout, stderr). Tests replace this module's RUN."""
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or PRODUCTION,
                       env={**os.environ, **(env or {})})
    return r.returncode, r.stdout, r.stderr


RUN = run_cmd


def module(name, *args):
    return [sys.executable, "-m", f"rpresolve.cmd_{name}", *args]


def load_config(path=None, overrides=None):
    """The pipeline config: the file named by `path` or $RP_PIPELINE_CONFIG (none is fine),
    with command-line values over it. Returns a dict of CONFIG_KEYS (missing ones are None)."""
    path = path or os.environ.get(CONFIG_ENV)
    cfg = {}
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            raise ProjectError(f"{path}: cannot read the pipeline config ({e})")
        if not isinstance(cfg, dict):
            raise ProjectError(f"{path}: a pipeline config is a JSON object")
        strangers = sorted(k for k in cfg if k not in CONFIG_KEYS + READ_ELSEWHERE and not k.startswith("_"))
        if strangers:
            raise ProjectError(f"{path}: unknown key(s) {', '.join(strangers)} (known: {', '.join(CONFIG_KEYS + READ_ELSEWHERE)})")
    out = {k: cfg.get(k) for k in CONFIG_KEYS}
    out.update({k: v for k, v in (overrides or {}).items() if v})
    for k in ("landing_root", "work_root", "sheets_root", "music_root"):
        if out[k]:
            out[k] = os.path.abspath(os.path.expanduser(out[k]))
    return out


def indexer_command(cfg):
    """(argv, env) of the configured indexer, or (None, {}). The config gives a list, a
    string, or {"argv": [...], "env": {...}}; paths may start with ~."""
    v = cfg.get("indexer")
    if not v:
        return None, {}
    env = {}
    if isinstance(v, dict):
        env, v = {k: os.path.expanduser(str(x)) for k, x in (v.get("env") or {}).items()}, v.get("argv")
    argv = v.split() if isinstance(v, str) else list(v or [])
    return ([os.path.expanduser(a) for a in argv] or None), env


# ---- the state file ----------------------------------------------------------

def state_path(cfg, slug):
    if not cfg["work_root"]:
        raise ProjectError("no work root: give --work-root or set work_root in the pipeline config")
    return os.path.join(cfg["work_root"], slug, STATE)


def review_log_path(cfg, slug):
    """The one review log of a project: <work root>/<slug>/review/events.jsonl, beside project.json."""
    if not SLUG.match(slug):
        raise ProjectError(f"{slug!r} is not a slug (lower-case words joined by hyphens)")
    return os.path.join(os.path.dirname(state_path(cfg, slug)), "review", "events.jsonl")


def new_state(slug):
    return {"schema": 1, "slug": slug, "cards": [], "open": [],
            "steps": {k: {"status": "pending", "note": ""} for k, _ in STEPS}}


def read_state(path, slug):
    if not os.path.isfile(path):
        return new_state(slug)
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        assert isinstance(st, dict) and isinstance(st.get("steps"), dict)
    except (OSError, ValueError, AssertionError):
        raise ProjectError(f"{path}: not a readable project state")
    for k, _ in STEPS:
        st["steps"].setdefault(k, {"status": "pending", "note": ""})
    return st


def write_state(path, st):
    rpcard.refuse_git(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".project-", suffix=".part")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


# ---- the chain ---------------------------------------------------------------

BATCH = 10      # clips a detect run reads before a count line
HEARTBEAT = 30  # seconds between "still working" lines


def progress(msg):
    print(msg, file=sys.stderr, flush=True)


class Ticker:
    """A line to `tick` every HEARTBEAT seconds while a long command runs, with `what` and the
    seconds so far (and `count()` when it gives one)."""

    def __init__(self, tick, what, count=None, every=None):
        self.tick, self.what, self.count, self.every = tick, what, count, every or HEARTBEAT
        self.began, self.stop = time.monotonic(), threading.Event()
        self.thread = threading.Thread(target=self.beat, daemon=True)

    def beat(self):
        while not self.stop.wait(self.every):
            n = f", {self.count()}" if self.count else ""
            self.tick(f"  {self.what}: still working, {time.monotonic() - self.began:.0f} s{n}")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()


def inside_work_root(cfg, target):
    """X when `target` lies inside <work root>/X/ and X is a slug, else None."""
    root = cfg.get("work_root")
    if not root:
        return None
    rel = os.path.relpath(target, root)
    top = rel.split(os.sep)[0]
    return top if not rel.startswith("..") and rel != "." and os.sep in rel and SLUG.match(top) else None


class Stop(Exception):
    """The chain stops here; the message says what it needs."""


class Chain:
    def __init__(self, cfg, slug, apply, say=print, tick=progress):
        self.cfg, self.slug, self.apply, self.say, self.tick = cfg, slug, apply, say, tick
        self.path = state_path(cfg, slug)
        self.st = read_state(self.path, slug)
        self.card = None

    def save(self):
        if self.apply:
            write_state(self.path, self.st)

    def mark(self, key, status, note=""):
        self.st["steps"][key] = {"status": status, "note": note}
        if key in PER_CARD and status in ("done", "skipped") and self.card:
            held = self.st.setdefault("card_done", {}).setdefault(key, [])
            if self.card not in held:
                held.append(self.card)
        self.save()

    def add_card(self, card):
        """Hold a card; a new one makes the per-card steps pending again."""
        self.card = card
        if card not in self.st["cards"]:
            self.st["cards"].append(card)
            for k in PER_CARD + ("ask",):
                self.st["steps"][k] = {"status": "pending", "note": f"waiting on card {card}"}

    def done(self, key):
        """A per-card step is done for the card in hand; a state from before cards were tracked
        counts its done steps for the first card."""
        if key in PER_CARD:
            held = self.st.get("card_done", {}).get(key)
            if held is None and self.st["steps"][key]["status"] in ("done", "skipped"):
                held = self.st["cards"][:1]
            return self.card in (held or [])
        return self.st["steps"][key]["status"] in ("done", "skipped")

    def cmd(self, argv, writes=False, **kw):
        """Run a command. A writing one only with apply; otherwise say it would run."""
        text = " ".join(argv)
        if writes and not self.apply:
            self.say(f"  would run: {text}")
            return None
        with Ticker(self.tick, argv[2] if argv[1:2] == ["-m"] else os.path.basename(argv[0])):
            rc, out, err = RUN(argv, **kw)
        return rc, out, err


def _sheets_out(cfg, slug, what):
    return os.path.join(cfg["sheets_root"], slug, what) if cfg["sheets_root"] else None


def step_copy(ch, target, cam):
    if target in ch.st["cards"]:
        ch.add_card(target)
        ch.mark("copy", "done", "card " + target)
        return target
    if os.path.isfile(target + rpcard.RECORD):
        ch.add_card(target)
        ch.mark("copy", "skipped", "a card folder with its record: not copied again")
        return target
    if ch.st["cards"] and not cam:  # a volume again, with no --cam: the resume of the copy made earlier
        ch.add_card(ch.st["cards"][-1])
        ch.mark("copy", "done", "card " + ch.card)
        return ch.card
    if not cam:
        raise Stop("needs --cam CAM (2 to 12 of A-Z 0-9) to copy a card volume")
    land = ch.cfg["landing_root"]
    if not land:
        raise Stop("needs a landing root (--landing, or landing_root in the pipeline config)")
    rc, out, err = RUN([OFFLOAD, "id", target])
    ids = dict(l.split(" ", 1) for l in out.splitlines() if " " in l)
    if rc != 0 or "card_id" not in ids:
        raise Stop(f"offload.sh id failed: {(err or out).strip()[-200:]}")
    dest = os.path.join(land, f"{cam}-{ids['card_id'][:6]}")
    if os.path.isfile(dest + rpcard.RECORD):
        ch.add_card(dest)
        ch.mark("copy", "done", "already copied to " + dest)
        return dest
    res = ch.cmd([OFFLOAD, "copy", target, land, "--cam", cam], writes=True)
    if res is None:
        raise Stop(f"dry run: the chain goes on from the copy at {dest} once --apply has run it")
    rc, out, err = res
    if rc != 0:
        raise Stop(f"offload.sh copy failed ({rc}): {(err or out).strip()[-200:]}")
    ch.add_card(dest)
    ch.mark("copy", "done", "copied to " + dest)
    return dest


def step_identify(ch, card):
    c = rpcard.load(card)
    if (c["record"] or {}).get("project") == ch.slug:
        ch.say("  project already recorded")
    else:
        rc, out, err = ch.cmd(module("card", "set", card, "--project", ch.slug, *(["--apply"] if ch.apply else [])))
        if rc != 0:
            raise Stop(f"card set --project failed: {(err or out).strip()[-200:]}")
        ch.say("  " + out.strip().replace("\n", "\n  "))
    files = [os.path.join(card, k["relpath"]) for k in c["clips"]] or [card]
    rows, done = [], 0
    with Ticker(ch.tick, "detect", lambda: f"{done} of {len(files)} clips"):
        for i in range(0, len(files), BATCH):
            rc, out, err = RUN(module("detect", *files[i:i + BATCH], "--json"))
            if rc == 1:
                raise Stop(f"detect failed: {(err or out).strip()[-200:]}")
            try:
                rows += json.loads(out)
            except ValueError:
                raise Stop("detect printed no JSON")
            done = min(i + BATCH, len(files))
            ch.tick(f"  detect: {done} of {len(files)} clips")
    review = sorted(r["path"] for r in rows if r.get("profile") in ("review", "corrupt"))
    ch.st["detect_review"] = review
    ch.say(f"  detect: {len(rows)} clip(s), {len(review)} for review")
    ch.mark("identify", "done", f"{len(rows)} clips, {len(review)} for review")


def step_ask(ch, card):
    c = rpcard.load(card)
    squeeze = [o for o in c["open"] if o.startswith("squeeze ")]
    ch.st["open"] = squeeze + [f"profile {os.path.basename(p)}" for p in ch.st.get("detect_review", [])]
    if not squeeze:
        ch.mark("ask", "done", "squeeze answered for every clip"
                + (f"; {len(ch.st['detect_review'])} detect review row(s) listed in open" if ch.st.get("detect_review") else ""))
        return
    names = [next((k["relpath"] for k in c["clips"] if k["clip_id"] == o[len("squeeze "):]), None) for o in squeeze]
    only = [os.path.join(card, n) for n in names if n]
    out = _sheets_out(ch.cfg, ch.slug, "questions")
    ch.say(f"  open: squeeze for {len(squeeze)} clip(s): " + ", ".join(o[len('squeeze '):] for o in squeeze[:6])
           + (" ..." if len(squeeze) > 6 else ""))
    if out and only:
        res = ch.cmd(module("sheets", card, "-o", out, *[a for p in only for a in ("--only", p)]), writes=True)
        if res and res[0] == 0:
            ch.say(f"  sheets of those clips: {out}")
    ch.mark("ask", "waiting", f"{len(squeeze)} clip(s) need a squeeze answer")
    raise Stop(f"needs a squeeze answer for {len(squeeze)} clip(s): "
               f"project answer {ch.slug} --squeeze F [--clip ID ...] --apply (1 is spherical)")


def step_index(ch, card):
    argv, env = indexer_command(ch.cfg)
    if not argv:
        ch.mark("index", "skipped", "no indexer configured")
        ch.say("  no indexer configured: skipped")
        return
    res = ch.cmd(argv + ["index", "--card", card], writes=True, env=env)
    if res is None:
        return
    rc, out, err = res
    if rc != 0:
        raise Stop(f"the indexer failed ({rc}): {(err or out).strip()[-200:]}")
    ch.mark("index", "done", "indexed")


def step_sheets(ch, card):
    out = _sheets_out(ch.cfg, ch.slug, "sheets")
    if not out:
        ch.mark("sheets", "skipped", "no sheets root configured")
        ch.say("  no sheets root: skipped")
        return
    res = ch.cmd(module("sheets", card, "-o", out), writes=True)
    if res is None:
        return
    rc, o, e = res
    if rc != 0:
        raise Stop(f"sheets failed ({rc}): {(e or o).strip()[-200:]}")
    ch.say("  " + o.strip())
    ch.mark("sheets", "done", out)


def resolve_call(slug):
    return json.dumps({"tool": "set_color_management", "project": "<the project's name in Resolve>",
                       "preset": "managed-dwg", "dry_run": True})


def step_resolve(ch):
    if ch.st["steps"]["resolve"]["status"] == "done":
        return
    ch.say("  a person creates the empty project in Resolve (the API cannot); then: " + resolve_call(ch.slug))
    ch.mark("resolve", "waiting", "waits on a person creating the project")
    raise Stop(f"needs a person to create the Resolve project, then run set_color_management; "
               f"project answer {ch.slug} --resolved --apply records it")


def start(cfg, target, slug, cam=None, apply=False, say=print, new_project=False, tick=progress):
    """Run the chain from `target` (a card volume, or a card folder with its record).
    The project is the folder `target` sits in under the work root, when it does: a different
    `slug` is refused unless new_project. Returns 0 when it reached the hand steps, 2 when it
    stopped for a person."""
    target = os.path.abspath(target).rstrip(os.sep)
    if not os.path.isdir(target):
        raise ProjectError(f"{target}: not a folder")
    here = inside_work_root(cfg, target)
    if here and slug and slug != here and not new_project:
        raise ProjectError(f"the card is in the work folder '{here}' but --project is '{slug}'; "
                           f"use --project {here}, or --new-project for a second project")
    slug = slug or here
    if not slug and os.path.isfile(target + rpcard.RECORD):
        slug = (rpcard.load(target)["record"] or {}).get("project")
    if not slug:
        raise ProjectError("needs a project slug: --project SLUG")
    if not SLUG.match(slug):
        raise ProjectError("a project is a slug: a-z, 0-9 and hyphens")
    ch = Chain(cfg, slug, apply, say, tick)
    say(f"project {slug} ({'apply' if apply else 'dry run'}), state {ch.path}")
    try:
        card = None
        for key, title in STEPS[:6]:
            if key in PER_CARD and ch.done(key):
                say(f"{key}: done")
                continue
            say(f"{key}: {title}")
            if key == "copy":
                card = step_copy(ch, target, cam)
            else:
                {"identify": step_identify, "ask": step_ask, "index": step_index,
                 "sheets": step_sheets, "resolve": lambda c, _card: step_resolve(c)}[key](ch, card)
        say("steps 7 and 8: the shot list is by hand; then project build (project status " + slug + ")")
        return 0
    except Stop as s:
        say(f"stopped: {s}")
        return 2


def _name_parts(pairs):
    """{"client": "X", "episode": 3} from ["client=X", "episode=3"]: episode and index are numbers."""
    out = {}
    for p in pairs:
        k, eq, v = p.partition("=")
        if not eq or not k or not v:
            raise ProjectError(f"--name-part {p!r}: use KEY=VALUE")
        out[k] = int(v) if k in ("episode", "index") and v.isdigit() else v
    return out


def _record(ch, key, value, say, where):
    """Record an answer once in `where` (the project's answers or one piece's), with who and
    when; a second one is refused, not overwritten."""
    held = where.get(key)
    if held:
        raise ProjectError(f"{key} is already recorded by {held['by']} at {held['at']}; "
                           f"edit {ch.path} by hand to change it")
    where[key] = {**value, "by": getpass.getuser(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ch.save()
    say(f"{key}: recorded" + ("" if ch.apply else " (dry run: not written)"))


def _answers(st, piece=None):
    """The answers a build of `piece` uses: the project's, with the piece's own over them."""
    if not piece:
        return st.get("answers", {})
    out = {**st.get("answers", {}), **st.get("pieces", {}).get(piece, {})}
    keep = (st.get("answers", {}).get("pick") or {}).get("exclude")
    if keep and out.get("pick") and not out["pick"].get("exclude"):
        out["pick"] = {**out["pick"], "exclude": keep}
    return out


def _pick(manifest, ledger, exclude, draft):
    """The picked manifest with its draft farm ledger and the exclude list, as absolute paths."""
    if not manifest:
        raise ProjectError("--ledger, --draft and --exclude go with --pick MANIFEST")
    out = {}
    for k, v in (("manifest", manifest), ("ledger", ledger), ("exclude", exclude)):
        if v:
            out[k] = os.path.abspath(os.path.expanduser(v))
            if not os.path.isfile(out[k]):
                raise ProjectError(f"--{'pick' if k == 'manifest' else k} {out[k]}: no such file")
    if draft:
        if not ledger:
            raise ProjectError("--draft names a plan in --ledger DRAFTS")
        out["draft"] = draft
    return out


def render_folder(cfg, slug, key, name=None):
    """Where the render of destination `key` goes, read as queue_render reads it (the resolve
    config with the $RPRESOLVE_CONFIG overlay): (target_dir to pass it or None when the
    destination's own is used, the folder the file lands in, why it would refuse or None, the
    file's path when `name` parts are given and fit). A destination with no target_dir takes
    <work root>/<slug>/renders; a state always has a work root (state_path)."""
    from . import deliver
    from .mcp.tools_offline import deliver_config
    try:
        dest = deliver.destination(deliver_config(), key)
        filename = deliver.name_for(dest, name) if name is not None else None
    except ValueError as e:  # DeliverError is one
        return None, None, str(e), None
    target = None if dest.get("target_dir") else os.path.join(os.path.abspath(cfg["work_root"]), slug, "renders")
    folder = deliver.output_folder(target or dest["target_dir"], dest)
    return target, folder, None, filename and os.path.join(folder, filename)


def _squeeze_held(card, clips):
    """Clip ids of a loaded card whose squeeze answer a `card set --squeeze` would replace:
    those named by id or file name, or with no --clip every clip without an answer of its own
    (all of them when each has its own: a default then changes nothing and is a second answer)."""
    own = ((card["answers"].get("squeeze") or {}).get("clips") or {})
    if clips:
        hit = [c for c in card["clips"] if c["clip_id"] in clips or os.path.basename(c["relpath"]) in clips]
    else:
        hit = [c for c in card["clips"] if c["clip_id"] not in own] or card["clips"]
    return [c["clip_id"] for c in hit if rpcard.squeeze_answer(card, c)[0] is not None]


def _music_pick(cfg, key_dir, letter, start=None):
    """The pick of a `music candidates` packet: the key.json beside its excerpts says which
    track the letter stood for, relative to the music root; the answer holds its absolute path."""
    if not key_dir or not letter:
        raise ProjectError("a music pick needs both --music-key PACKET and --music-pick LETTER")
    try:
        with open(os.path.join(os.path.abspath(os.path.expanduser(key_dir)), "key.json"), encoding="utf-8") as f:
            key = json.load(f)
        o = key["options"][letter.upper()]
    except (OSError, ValueError, KeyError) as e:
        raise ProjectError(f"{key_dir}: no key.json with letter {letter} ({e})")
    if not cfg.get("music_root"):
        raise ProjectError("no music root: give music_root in the pipeline config")
    path = os.path.join(cfg["music_root"], o["path"])
    if not os.path.isfile(path):
        raise ProjectError(f"{path}: the picked track is not there")
    return {"path": path, "in": float(key.get("in", 0) if start is None else start), "letter": letter.upper()}


def answer(cfg, slug, apply, squeeze=None, clips=(), resolved=False, say=print,
           shape=None, look=None, destination=None, name_parts=(), music=None, pick=None, piece=None,
           replace=False, clear_music=False):
    """Record an answer through `card set`, that the Resolve project is set up, or one of the
    build answers (shape, look, destination, music, the picked manifest) in the project state:
    the project's, or with `piece` that piece's own, which a build of it reads over the project's."""
    ch = Chain(cfg, slug, apply, say)
    if piece and not SLUG.match(piece):
        raise ProjectError(f"--piece {piece!r}: a piece is a slug (lower-case words joined by hyphens)")
    where = ch.st.setdefault("pieces", {}).setdefault(piece, {}) if piece else ch.st.setdefault("answers", {})
    if clear_music:
        real = lambda m: m and not m.get("none")  # a piece's "no music" marker is not a pick
        inherited = bool(piece) and real(ch.st.get("answers", {}).get("music"))
        if not real(where.get("music")) and not (inherited and "music" not in where):
            raise ProjectError("no music pick is recorded" + (f" for piece {piece!r}" if piece else ""))
        where.pop("music", None)
        if inherited:  # an explicit "no music" for the piece, over the project's pick
            where["music"] = {"none": True, "by": getpass.getuser(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        ch.save()
        say("music: pick cleared" + ("" if apply else " (dry run: not written)"))
        return 0
    held = [k for k, v in (("shape", shape), ("look", look), ("destination", destination), ("music", music),
                           ("pick", pick)) if v and k in where]
    if held:  # refuse before writing any of them
        raise ProjectError(f"{held[0]} is already recorded by {where[held[0]]['by']} at "
                           f"{where[held[0]]['at']}; edit {ch.path} by hand to change it")
    todo = []  # every answer checked before any is written, so a bad one leaves none half-recorded
    if shape:
        m = re.fullmatch(r"(\d+)x(\d+)", shape)
        if not m:
            raise ProjectError(f"--shape {shape!r}: use WIDTHxHEIGHT, such as 1920x1080")
        todo.append(("shape", {"width": int(m[1]), "height": int(m[2])}))
    if look:
        todo.append(("look", {"lut": look}))
    if destination:
        todo.append(("destination", {"key": destination, "name": _name_parts(name_parts)}))
    if music:
        todo.append(("music", _music_pick(cfg, *music)))
    if pick:
        todo.append(("pick", _pick(*pick)))
    for key, value in todo:
        _record(ch, key, value, say, where)
    if todo:
        return 0
    if piece:
        raise ProjectError("--piece goes with the build answers (shape, look, destination, music, pick)")
    if not ch.st["cards"]:
        raise ProjectError(f"{slug}: no card in {ch.path}; run project start first")
    if resolved:
        ch.mark("resolve", "done", "project created and colour set by a person")
        say("resolve: recorded" + ("" if apply else " (dry run: not written)"))
    if squeeze is not None:
        for card in ch.st["cards"] if not replace else ():
            held = _squeeze_held(rpcard.load(card), set(clips))
            if held:
                raise ProjectError(f"{os.path.basename(card)}: squeeze is already answered for {len(held)} clip(s) "
                                   f"({', '.join(held[:3])}{' ...' if len(held) > 3 else ''}); "
                                   "give --replace to change it")
        args = ["--squeeze", f"{squeeze:g}"] + [a for c in clips for a in ("--clip", c)]
        for card in ch.st["cards"]:
            rc, out, err = RUN(module("card", "set", card, *args, *(["--apply"] if apply else [])))
            say(out.strip() or err.strip())
            if rc != 0:
                return 1
    return 0


def unsafe_to_format(cards):
    """Why the cards may not be formatted, or "" when every one may: each must load, carry an offsite.txt
    that names it, and have no record changed since that check. Fails closed on no cards or an unreadable one."""
    if not cards:
        return "this project holds no card"
    for card in cards:
        try:
            c = rpcard.load(card)
        except (rpcard.CardError, OSError) as e:
            return f"{card} cannot be read ({e})"
        if not c["offsite"]["ok"]:
            return f"no offsite.txt names {card}"
        stale = [o for o in c["open"] if o.startswith("offsite record")]
        if stale:
            return f"{card}: {stale[0]}"
    return ""


def status(cfg, slug, say=print):
    """The nine steps with their state; for 7 to 9 the next command (none for 7 once a pick is
    recorded, only the build for 8 once its answers are); each answer as recorded and those still
    open; a recorded destination with no render folder; whether the card may be formatted (true
    only when every card has an offsite.txt that names it and no changed record)."""
    ch = Chain(cfg, slug, False, say)
    if not os.path.isfile(ch.path):
        raise ProjectError(f"{slug}: no {ch.path}; run project start first")
    ans, pieces = ch.st.get("answers", {}), ch.st.get("pieces", {})
    levels = [ans] + [_answers(ch.st, p) for p in pieces]
    ready = any(all(k in a for k, _, _ in ANSWERS) for a in levels)
    for i, (key, title) in enumerate(STEPS, 1):
        s = ch.st["steps"][key]
        say(f"{i}. {title}: {s['status']}" + (f" ({s['note']})" if s["note"] else ""))
        if key in NEXT and s["status"] != "done" and not (key == "shotlist" and any("pick" in a for a in levels)):
            say(f"   next: {BUILD_NEXT if key == 'build' and ready else NEXT[key]}")
    for o in ch.st.get("open", []):
        say(f"open: {o}")

    def shown(v):
        return json.dumps({k: x for k, x in v.items() if k not in ("by", "at")}) + f" ({v['by']}, {v['at']})"
    for key, v in ans.items():
        say(f"recorded: {key}: {shown(v)}")
    for p, held in sorted(pieces.items()):
        for key, v in held.items():
            say(f"recorded for piece {p}: {key}: {shown(v)}")
    for key, title, how in ANSWERS:  # open unless the project holds it or every piece does
        if key not in ans and not (pieces and all(key in held for held in pieces.values())):
            say(f"open: {title}: project answer {slug} {how} --apply")
    for key in sorted({a["destination"]["key"] for a in levels if "destination" in a}):
        _, folder, problem, _ = render_folder(cfg, slug, key)
        say(f"open: render folder: {problem}" if problem else f"render folder for '{key}': {folder}")
    why = unsafe_to_format(ch.st["cards"])
    say("may the card be formatted: " + (f"no, {why}" if why
        else "yes, every card has an offsite.txt that names it"))
    if not any("music" in a for a in levels) and cfg.get("music_root") \
            and os.path.isfile(os.path.join(cfg["music_root"], "library.json")):
        say(f"music (optional): python3 resolve_workflow.py music candidates {cfg['music_root']} --manifest M --out DIR; "
            f"then project answer {slug} --music-key DIR --music-pick LETTER --apply")
    return 0


# ---- the last stretch: a picked draft to a queued render --------------------

def call_tool(name, args):
    """Run one MCP tool's own handler in this process, args checked against its schema."""
    from .mcp import schema, server
    from .mcp.registry import ToolContext
    from .mcp.session import ResolveSession
    tool = server.build_registry().get(name)
    args = schema.with_defaults(tool.input_schema, args)
    problems = schema.validate(tool.input_schema, args)
    if problems:
        raise ProjectError(f"{name}: {'; '.join(problems)}")
    return tool.handler(args, ToolContext(session=_SESSION))


def open_project_name():
    from . import api
    return api.current_project(_SESSION.get())[1].GetName()


def _session():
    from .mcp.session import ResolveSession
    return ResolveSession()


_SESSION = None
CALL = call_tool
OPEN_NAME = open_project_name


def build(cfg, slug, manifest=None, name=None, apply=False, say=print, piece=None):
    """assemble_timeline, each of the manifest's effects, apply_grade with the recorded look,
    queue_render for the recorded destination: the tools' own handlers, in order, the first
    failure stops. The manifest is `manifest` or the recorded pick, whose ledger and exclude
    list go to the assemble; with `piece`, that piece's answers over the project's. Before
    anything, it refuses a destination queue_render would refuse for want of a folder (one with no
    target_dir takes <work root>/<slug>/renders, made on --apply), and a manifest with its own
    music when a music pick is recorded. A dry
    run prints every step's dry run (a step after the assemble that needs the timeline to exist
    says so); --apply runs each for real with the plan_sha of its own dry run."""
    global _SESSION
    ch = Chain(cfg, slug, apply, say)
    if piece and piece not in ch.st.get("pieces", {}):
        raise ProjectError(f"no answers recorded for piece {piece!r}: project answer {slug} --piece {piece} ... --apply")
    ans = _answers(ch.st, piece)
    piece_flag = f" --piece {piece}" if piece else ""
    missing = [f"{t}: project answer {slug}{piece_flag} {h} --apply" for k, t, h in ANSWERS if k not in ans]
    if missing:
        raise ProjectError("not recorded, so nothing was built: " + "; ".join(missing))
    d = ans["destination"]
    target, folder, problem, out_file = render_folder(cfg, slug, d["key"], d["name"])
    if problem:
        raise ProjectError(f"nothing was built: queue_render would refuse at the end: {problem}")
    for p in ch.st.get("pieces", {}):  # two pieces that resolve to one file would overwrite each other
        if p != piece and "destination" in _answers(ch.st, p):
            o = _answers(ch.st, p)["destination"]
            if render_folder(cfg, slug, o["key"], o["name"])[3] == out_file:
                raise ProjectError(f"nothing was built: piece {p!r} renders to the same file ({out_file}); "
                                   "give one of them other name parts")
    if target and apply:  # before the assemble, so a folder that cannot be made stops it all
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            raise ProjectError(f"nothing was built: cannot make the render folder {target} ({e})")
    pick = ans.get("pick") or {}
    manifest = manifest or pick.get("manifest")
    if not manifest:
        raise ProjectError(f"build needs --manifest PATH, or project answer {slug}{piece_flag} --pick MANIFEST --apply")
    manifest = os.path.abspath(os.path.expanduser(manifest))
    checks = {"exclude": pick["exclude"]} if pick.get("exclude") else {}
    if pick.get("ledger") and manifest == pick["manifest"]:  # a ledger belongs to its own manifest
        checks.update(ledger=pick["ledger"], **({"draft": pick["draft"]} if pick.get("draft") else {}))
    try:
        with open(manifest, encoding="utf-8") as f:
            doc = json.load(f)
        effects = doc.get("effects") or []
    except (OSError, ValueError) as e:
        raise ProjectError(f"{manifest}: cannot read the manifest ({e})")
    scratch = None
    music = ans.get("music")
    music = None if music and music.get("none") else music
    if music:
        own = doc.get("music")
        if own and (os.path.abspath(str(own.get("path"))) != music["path"] or float(own.get("in") or 0) != float(music["in"])):
            raise ProjectError(
                f"nothing was built: the manifest has its own music ({os.path.basename(str(doc['music'].get('path')))}) "
                f"and a pick is recorded ({os.path.basename(music['path'])}); settle it once: remove the music "
                f"from the manifest (then the pick goes in), or clear the pick with "
                f"project answer {slug}{piece_flag} --clear-music --apply")
        elif not own:  # the picked manifest is never edited: build from a copy that holds the pick
            doc["music"] = {"path": music["path"], "in": music["in"]}
            # beside the state on --apply; a dry run needs a real file for the tool's dry run, so a temp one
            scratch = None if apply else tempfile.mkdtemp(prefix="build-")
            manifest = os.path.join(scratch or os.path.dirname(ch.path), "build-manifest.json")
            write_state(manifest, doc)
            say(f"music: the recorded pick goes in as {manifest}")
    try:
        tl = name or piece or slug
        tl = tl if tl.endswith(" [auto]") else tl + " [auto]"
        _SESSION = _SESSION or _session()
        base = {"project": OPEN_NAME()}
        say(f"build {slug} ({'apply' if apply else 'dry run'}): '{tl}' in Resolve project '{base['project']}'")
        say(f"render folder: {folder}")

        def step(tool, args, after=False):
            try:
                r = CALL(tool, {**base, **args, "dry_run": True})
            except Exception as e:
                # only the missing timeline waits for the build; any other refusal (a render
                # destination with no folder) would stop --apply after the assemble, so say it now
                if after and not apply and re.search(r"no (such )?timeline", str(e)):
                    say(f"  {tool}: not checkable until '{tl}' is built ({e}); would run with {json.dumps(args)}")
                    return None
                raise ProjectError(f"{tool}: {e}")
            say(f"  {tool}: {r['summary']}")
            if not apply:
                return r
            try:
                r = CALL(tool, {**base, **args, "dry_run": False, "plan_sha": r["plan_sha"]})
            except Exception as e:
                raise ProjectError(f"{tool}: {e}")
            say(f"  {tool}: {r['summary']}")
            bad = [x for x in r.get("results", []) if not x.get("ok", True)]
            if r.get("problems") or r.get("leaks") or bad or r.get("exit_status", 0):
                raise ProjectError(f"{tool}: the run did not come out clean; stopped before the next step")
            return r

        sh = ans["shape"]
        r = step("assemble_timeline", {"manifest": manifest, "name": tl, "width": sh["width"], "height": sh["height"],
                                       **checks})
        tl = r["timeline"] if isinstance(r.get("timeline"), str) else tl
        track = r["plan"]["track"]
        if apply:
            ch.st["built"] = {"timeline": tl}
            ch.mark("build", "done", f"built '{tl}'")
        try:
            _rest(step, ch, ans, effects, tl, track, apply, say, target)
        except ProjectError as e:
            if apply:
                raise ProjectError(f"{e}; '{tl}' is built and stays in Resolve: finish the remaining steps by "
                                   "hand or build again with a new --name")
            raise
        return 0
    finally:
        if scratch:  # the dry run's copy holds a client's music path; nothing else is in it
            shutil.rmtree(scratch, ignore_errors=True)


def _rest(step, ch, ans, effects, tl, track, apply, say, target=None):
    for e in effects:
        step("apply_effect", {"timeline": tl, "effect": e["effect"], "track": track, "spans": [e["span_index"]],
                              **({"params": e["params"]} if e.get("params") else {})}, after=True)
    for t in sorted({1, track}):
        step("apply_grade", {"timeline": tl, "track": t, "lut": {"path": ans["look"]["lut"]}}, after=True)
    d = ans["destination"]
    args = {"timeline": tl, "destination": d["key"], "name": d["name"], **({"target_dir": target} if target else {})}
    if target and not apply and not os.path.isdir(target):  # queue_render wants the folder to exist; a dry run makes none
        say(f"  queue_render: not checkable until {target} is made on --apply; would run with {json.dumps(args)}")
        return
    r = step("queue_render", args, after=True)
    if apply:
        job = (r.get("job") or {}).get("JobId")
        ch.st["built"]["job"] = job
        ch.mark("deliver", "queued", f"job {job}, waiting for a person to start the render")
        say("queued, not started: a person starts the render")
