#!/usr/bin/python3
"""
Live check of the Resolve MCP server against a sandbox project. Run by
hand with Resolve open on the sandbox; CI never runs it.

    /usr/bin/python3 production/tests/live_mcp_sandbox.py \\
        --project "RP Automation Sandbox" --project-id <unique id> \\
        --timeline "<an existing timeline to copy>" \\
        --lut "<a .cube, absolute or relative to Resolve's LUT folder>" \\
        [--drx <.drx with its .json label manifest beside it>] \\
        [--render-dir <existing folder outside any git repo; a client_master job is queued for it>] \\
        [--ingest <camera file not yet in the pool>] \\
        [--manifest <cut manifest> --clip <clip name>] [--captions] \\
        [--bin <a media-pool bin of video clips; timeline_from_clips builds from it>]

It refuses unless the open project's name contains "Sandbox" and its
unique id is --project-id. It snapshots every timeline (items, frames and
grade fingerprints), every pool clip's properties, the UI, the render
queue and the Deliver format; then drives the server over stdio through
every tool, dry run then real run for the writes, plus the refusals
(a non-[auto] grade, the wrong project, a stale plan_sha). Last, it deletes
the one render job it queued (and the empty folder that job's destination
made) and checks that nothing that existed before changed and that the UI
is where it was. New timelines are named
"MCP live <time> ... [auto]" and stay in the sandbox. With --captions it
also runs create_captions on the copy (Resolve transcribes it, about as
long as the timeline runs); the copy must have audio and no subtitle items.

Exit 0 when every check passes, 1 otherwise.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import api, grade  # noqa: E402

SERVER = HERE.parent / "resolve_mcp.py"
# Counters Resolve updates itself when a new timeline uses a clip.
VOLATILE_CLIP_PROPS = {"Usage"}
FAILS = []


def check(ok, what):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(what)
    return ok


class Client:
    def __init__(self):
        self.proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                "clientInfo": {"name": "live_mcp_sandbox", "version": "1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def rpc(self, method, params):
        self.n += 1
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("the server exited")
            msg = json.loads(line)
            if msg.get("id") == self.n:
                return msg

    def tool(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        body = r.get("structuredContent") or {"summary": r["content"][0]["text"][:300]}
        print(f"  {name}{'' if args.get('dry_run', True) is not False else ' (real)'}: "
              f"{'ERROR ' if r['isError'] else ''}{body.get('summary', '')[:220]}")
        return r["isError"], body

    def both(self, name, **args):
        err, dry = self.tool(name, **args)
        if err or "plan_sha" not in dry:
            return err, dry
        return self.tool(name, **args, dry_run=False, plan_sha=dry["plan_sha"])

    def close(self):
        self.proc.stdin.close()
        return self.proc.wait(timeout=60)


def snapshot(project):
    """Everything a write must leave alone, keyed by unique id."""
    timelines = {}
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        items = {}
        for kind in ("video", "audio"):
            for t in range(1, int(tl.GetTrackCount(kind) or 0) + 1):
                for n, it in enumerate(tl.GetItemListInTrack(kind, t) or [], 1):
                    g = it.GetNodeGraph() if kind == "video" else None
                    items[f"{kind}{t}#{n}"] = (it.GetStart(), it.GetEnd(),
                                               it.GetSourceStartFrame(), it.GetSourceEndFrame(),
                                               grade.graph_fingerprint(g) if g else None)
        timelines[tl.GetUniqueId()] = (tl.GetName(), items)
    clips = {}

    def walk(folder):
        for c in folder.GetClipList() or []:
            clips[c.GetUniqueId()] = c.GetClipProperty()
        for f in folder.GetSubFolderList() or []:
            walk(f)
    walk(project.GetMediaPool().GetRootFolder())
    return {"timelines": timelines, "clips": clips,
            "jobs": [j["JobId"] for j in project.GetRenderJobList() or []],
            "format": project.GetCurrentRenderFormatAndCodec()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--lut", required=True)
    ap.add_argument("--drx")
    ap.add_argument("--render-dir")
    ap.add_argument("--ingest")
    ap.add_argument("--manifest")
    ap.add_argument("--clip")
    ap.add_argument("--bin", help="a media-pool bin of video clips for timeline_from_clips")
    ap.add_argument("--captions", action="store_true",
                    help="Also run create_captions on the copy")
    a = ap.parse_args()
    if "Sandbox" not in a.project:
        print("REFUSED: --project must be a sandbox (its name contains 'Sandbox').")
        return 1

    resolve = api.connect()
    with api.ResolveLock():
        pm, project = api.current_project(resolve)
        if project.GetName() != a.project or project.GetUniqueId() != a.project_id:
            print(f"REFUSED: the open project is '{project.GetName()}' "
                  f"({project.GetUniqueId()}), not the sandbox named.")
            return 1
        ui0 = (resolve.GetCurrentPage(), project.GetCurrentTimeline().GetUniqueId())
        before = snapshot(project)
    print(f"Snapshot: {len(before['timelines'])} timelines, {len(before['clips'])} clips, "
          f"{len(before['jobs'])} render jobs, UI {ui0[0]}")

    stamp = time.strftime("%H%M%S")
    dup_name = f"MCP live {stamp} [auto]"
    P = {"project": a.project, "project_id": a.project_id}
    c = Client()
    job_id = made_folder = None
    try:
        print("Reads")
        err, st = c.tool("resolve_status")
        check(not err and st["project"]["unique_id"] == a.project_id, "resolve_status names the sandbox")
        err, r = c.tool("list_timelines", **P, limit=500)
        check(not err and r["total"] == len(before["timelines"]), "list_timelines counts every timeline")
        plain = next((t["name"] for t in r.get("rows", []) if not t["auto"]), None)
        err, r = c.tool("timeline_items", **P, timeline=a.timeline, track=1, grades=False)
        check(not err and r["total"] > 0, "timeline_items reads the origin")
        v1_items = r.get("total", 0)
        check(not c.tool("media_pool", **P, depth=1)[0], "media_pool")
        check(not c.tool("render_queue_status", **P)[0], "render_queue_status")

        print("Refusals")
        if plain:
            err, r = c.tool("apply_grade", **P, timeline=plain, lut={"path": a.lut})
            check(err and "[auto]" in r["summary"], "apply_grade refuses a timeline that is not [auto]")
        err, r = c.tool("duplicate_timeline_auto", project="Some Other Project",
                        timeline=a.timeline, new_name=dup_name)
        check(err, "a write naming another project is refused")
        err, r = c.tool("duplicate_timeline_auto", **P, timeline=a.timeline, new_name=dup_name,
                        dry_run=False, plan_sha="0" * 64)
        check(err and "plan changed" in r["summary"], "a stale plan_sha is refused")

        # The sandbox holds work, so set_color_management must refuse it (or have nothing to do,
        # when it is already managed). Either way it writes nothing.
        err, r = c.tool("set_color_management", **P)
        check(err and "not a fresh project" in r["summary"] or
              not err and not r.get("would_change"),
              "set_color_management refuses a project that holds work, or has nothing to set")

        print("Writes")
        err, r = c.both("duplicate_timeline_auto", **P, timeline=a.timeline, new_name=dup_name)
        check(not err and r.get("mismatches") == [], "duplicate matches its origin")
        err, r = c.both("apply_grade", **P, timeline=dup_name, items=[1], lut={"path": a.lut})
        check(not err and r["results"][0]["ok"] and r["leaks"] == [], "LUT applied, read back, no leak")
        if a.drx and v1_items >= 2:
            err, r = c.both("apply_grade", **P, timeline=dup_name, items=[2], drx={"path": a.drx})
            check(not err and r["results"][0]["ok"] and r["leaks"] == [],
                  ".drx applied, labels read back, no leak")
        elif a.drx:
            print("  skip  .drx: the timeline has one item on V1 (the LUT took it)")
        if a.render_dir:
            err, r = c.both("queue_render", **P, timeline=dup_name, destination="client_master",
                            target_dir=a.render_dir,
                            name={"client": "McpLive", "slug": f"live-{stamp}"})
            job_id = (r.get("job") or {}).get("JobId")
            made_folder = r.get("made_folder")
            check(not err and job_id and r["readback_problems"] == [], "render queued and read back")
            restore = [w for w in r.get("warnings", []) if "back to" in w or "stays" in w]
            check(not restore, "Deliver format and render mode put back without warnings")
        if a.bin:
            tl_name = f"MCP live {stamp} ladder [auto]"
            err, r = c.both("timeline_from_clips", **P, name=tl_name, bin=a.bin)
            check(not err and r["problems"] == [] and r["created"] and
                  all(i["ok"] for i in r["created"]["items"]),
                  "timeline_from_clips built the bin in order and read every item back")
            check(not r.get("ui_restore_problems"), "its UI (timeline, playhead, page) was put back")
        if a.ingest:
            err, r = c.both("ingest", **P, paths=[a.ingest])
            check(not err and r["exit_status"] in (0, 2), "ingest ran")
        if a.captions:
            err, r = c.both("create_captions", **P, timeline=dup_name)
            check(not err and r.get("items", 0) > 0 and not r.get("ui_restore_problems"),
                  "captions made, read back from the subtitle track, UI put back")
        if a.manifest and a.clip:
            err, r = c.both("cut", **P, manifest=a.manifest, clips=[a.clip],
                            prefix=f"MCPlive{stamp}")
            check(not err and r["created"], "cut built its timelines")
    finally:
        code = c.close()
        # Always take the test job back out, even when a later step raised.
        with api.ResolveLock():
            pm, project = api.current_project(resolve)
            if job_id:
                rendering = project.IsRenderingInProgress()
                check(not rendering, "no render was started")
                if not rendering:
                    check(bool(project.DeleteRenderJob(job_id)), "the test render job was deleted")
            if made_folder:
                # The folder the destination made for the job; empty, as nothing rendered.
                try:
                    os.rmdir(made_folder)
                except OSError:
                    pass
    check(code == 0, "the server exited cleanly")

    with api.ResolveLock():
        pm, project = api.current_project(resolve)
        after = snapshot(project)
        ui1 = (resolve.GetCurrentPage(), project.GetCurrentTimeline().GetUniqueId())

    print("Nothing that existed changed")
    changed = [f"{v[0]}" for k, v in before["timelines"].items() if after["timelines"].get(k) != v]
    check(not changed, "every pre-existing timeline is unchanged" +
          (f" (changed: {', '.join(changed)})" if changed else ""))
    new = [after["timelines"][k][0] for k in after["timelines"] if k not in before["timelines"]]
    check(all(n.endswith(" [auto]") for n in new), f"every new timeline is [auto] ({len(new)} new)")
    changed = {}
    for k, v in before["clips"].items():
        now = after["clips"].get(k) or {}
        keys = sorted(key for key in set(v) | set(now)
                      if key not in VOLATILE_CLIP_PROPS and v.get(key) != now.get(key))
        if keys:
            changed[v.get("Clip Name") or k] = keys
    check(not changed, "every pre-existing clip's properties are unchanged" +
          (f" (changed: {changed})" if changed else ""))
    check(after["jobs"] == before["jobs"], "the render queue is as it was")
    check(after["format"] == before["format"], "the Deliver format and codec are as they were")
    check(ui1 == ui0, "the page and current timeline are as they were")
    print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed check(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    status = 1
    try:
        status = main()
    finally:
        sys.stdout.flush()
        os._exit(status)
