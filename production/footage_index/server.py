"""footage-recall MCP server: search the footage index from any Claude Code session.

Results name private and client media: keep them local, out of commits, shared documents and cloud sessions."""
import os

from mcp.server.fastmcp import FastMCP

from . import edit, roots, search, shoot, store

mcp = FastMCP("footage-recall")


def _slim(r):
    return {"shot_id": r["id"], "path": roots.volume_path(r["relpath"]), "in_s": r["t_in"],
            "out_s": r["t_out"], "keyframe_s": r["t"], "thumb": r["thumb"], "camera": r["camera"],
            "profile": r["profile"], "fps": r["fps"], "faces": r["faces"], "motion": r["motion"],
            "client": bool(r["client"]), "caption": r["caption"], "score": r["score"]}


@mcp.tool()
def search_footage(query: str, limit: int = 10, include_client: bool = True, fps_min: float | None = None,
                   camera: str | None = None, project: str | None = None, faces: bool | None = None,
                   moving: bool | None = None) -> list[dict]:
    """Find shots by description ("slow push-in on hands chopping herbs, tele").
    Hybrid of SigLIP image similarity and caption text. Client footage is included unless include_client=False.
    fps_min 50 finds slow-motion material. Each result has the file path, in/out seconds and a local thumbnail
    you can Read to look at."""
    c = store.connect()
    return [_slim(r) for r in search.find(c, query, limit=limit, client=include_client, fps_min=fps_min,
                                          camera=camera, project=project, faces=faces, moving=moving)]


@mcp.tool()
def similar_to(shot_id: int, limit: int = 10, include_client: bool = True) -> list[dict]:
    """Shots that look like a given shot (by its SigLIP embedding)."""
    c = store.connect()
    return [_slim(r) for r in search.similar(c, shot_id, limit=limit, client=include_client)]


@mcp.tool()
def get_clip(path_contains: str) -> list[dict]:
    """Clip metadata and every sampled shot for clips whose path contains the text."""
    c = store.connect()
    out = []
    for cl in c.execute("SELECT * FROM clips WHERE relpath LIKE ? LIMIT 5", (f"%{path_contains}%",)):
        shots = [dict(s) for s in c.execute("SELECT s.*, cap.data AS caption FROM shots s LEFT JOIN captions cap "
                                            "ON cap.shot_id = s.id WHERE clip_id = ? ORDER BY t", (cl["id"],))]
        out.append({**dict(cl), "shots": shots})
    return out


@mcp.tool()
def profile_shoot(prefix: str, refresh: bool = False) -> dict:
    """Context tags for one shoot (a folder prefix such as "Shoots/harvest-day"): travel, event, outdoor,
    people, food, night, motion, portrait, nature, each with a 0-1 strength and its evidence, plus fps mix and shares."""
    return shoot.profile(store.connect(), prefix, refresh=refresh)


@mcp.tool()
def rank_edit_methods(prefix: str) -> dict:
    """The 3 edit methods (toolkit production/edit-methods/) that best fit a shoot: tag overlap plus the share of
    slots whose best in-shoot match clears the score floor, each with its 4 strongest slot thumbnails."""
    return edit.rank(store.connect(), prefix)


@mcp.tool()
def plan_edit(method_id: str, prefix: str, music: str | None = None, swap_slot: int | None = None,
              swap_letter: str | None = None) -> dict:
    """Fill every slot of a method from one shoot; writes fills.json (and the v2 manifest when the toolkit has
    rpresolve.methods.to_manifest) to <edits_dir>/<shoot>-<method>/. swap_slot alone makes a lettered sheet of
    that slot's top 5; swap_slot with swap_letter puts that take in the slot."""
    c, m = store.connect(), edit.get_method(method_id)
    if swap_slot:
        return edit.swap(c, m, prefix, swap_slot, swap_letter)
    fills, d, wrote = edit.plan(c, m, prefix, music=music)
    return {"fills": fills, "dir": d, "wrote": wrote}


@mcp.tool()
def plan_variants(prefix: str, n: int = 6, music: str | None = None) -> dict:
    """n complete plans for one shoot that differ in method, slot fills, look and pacing (no two share more than
    half their takes), each in <edits_dir>/<shoot>-variants/vK-<method>/ with fills.json, method.json and the
    manifest, ranked against the taste profile and the review log. rpresolve.drafts renders them."""
    vs, root = edit.variants(store.connect(), prefix, n, music=music)
    return {"dir": root, "variants": vs}


@mcp.tool()
def footage_stats() -> dict:
    """How much footage is indexed: clips, shots, vectors, captions."""
    return store.stats(store.connect())


if __name__ == "__main__":
    mcp.run()
