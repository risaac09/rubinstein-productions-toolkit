# Spike plan: can the scripting API set the colour pipeline of record?

*Drafted 2026-10-16 (Amendment 2, item 16 follow-up, after #141). A plan, not a result. Nothing here has been run, and no preset key has been added.*

You run this in Resolve, in a throwaway project. The target is the Colour Pipeline of Record section of [resolve-template-spec.md](../resolve-template-spec.md). The question: can `set_color_management` take more than `colorScienceMode`, so that a fresh colour-managed project arrives with the timeline colour space, output colour space and tone and gamut mapping of the Colour Pipeline of Record ([resolve-template-spec.md](../resolve-template-spec.md)) instead of a hand step?

`rpresolve/colormgmt.py` allows a new preset key only after it has been set through the API on a live Resolve and read back. This plan is that check. It changes no tool behaviour.

## What Resolve's own documentation says

Read on this Mac on 2026-10-16, Resolve 21.1.1, in `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/`. Three files matter, and they disagree in age.

| File | Dated | What it gives |
|---|---|---|
| `README.txt` | 2 Aug 2026 | The older text. `Project.GetSetting(settingName)` and `Project.SetSetting(settingName, settingValue)`, and the same two on `Timeline`. Prose on key and value handling. Enumerated values only for `superScale`, `timelineFrameRate` and `timelineSampleRate`. |
| `README.md` | 1 Oct 2026 (21.1) | Marks `GetSetting` and `SetSetting` deprecated for `GetSettings()` and `SetSettings({settings})`. Says valid values are documented with the `ProjectSettings` and `TimelineSettings` types. |
| `DaVinciResolveScript.pyi` | 1 Oct 2026 | Those two types, with key names and a one-line docstring per key. This is the only place the colour keys appear. |

What is confirmed, quoted as written:

- **Key names, on both `ProjectSettings` and `TimelineSettings` unless noted:** `isAutoColorManage` ('0' or '1'), `rcmPresetMode`, `separateColorSpaceAndGamma` ('0' or '1'), `colorScienceMode`, `colorSpaceTimeline`, `colorSpaceTimelineGamma`, `colorSpaceInput` and `colorSpaceInputGamma` (project only), `colorSpaceOutput`, `colorSpaceOutputGamma`, `colorSpaceOutputToneMapping`, `colorSpaceOutputGamutMapping`, `inputDRT`, `outputDRT`, `useInverseDRT`, `timelineWorkingLuminanceMode`, `timelineWorkingLuminance`, `colorSpaceOutputToneLuminanceMax`, `colorSpaceOutputGamutSaturationKnee`, `colorSpaceOutputGamutSaturationMax`, `useColorSpaceAwareGradingTools`, `hdrMasteringOn`, `hdrMasteringLuminanceMax`. `colorSpaceOutputGamutLimit` ("Clipping color space") is on `TimelineSettings` only. These are the `.pyi` names, so they are the ones the API is documented to answer to.
- **`colorScienceMode` values:** 'davinciYRGB', 'davinciYRGBColorManaged', 'davinciYRGBColorManagedv2', 'acescc' or 'acescct'.
- **Tone mapping values:** `colorSpaceOutputToneMapping`, `inputDRT` and `outputDRT` each list 'None', 'Simple', 'Luminance Mapping', 'DaVinci', 'Saturation Preserving' or 'RED IPP2'.
- **Gamut mapping values:** `colorSpaceOutputGamutMapping` lists 'None', 'Saturation Mapping' or 'RED IPP2 Gamut Mapping'.
- **Examples in the docstrings:** `colorSpaceTimeline` is "e.g. 'Rec.709'", `colorSpaceTimelineGamma` is "e.g. 'Gamma 2.4'". `timelineWorkingLuminanceMode` is "e.g. 'SDR 100', 'HDR 1000', 'HDR ER 1000/4000' or 'Custom'".
- **The timeline level:** `Timeline.GetSettings()` returns the project settings while `useCustomSettings` is '0', and timeline settings other than `useCustomSettings` can be changed only when it is '1'. `SetSettings` applies keys one by one in no stated order, keeps those applied before a failure, and returns False with a message naming the failed keys.
- **Read-only cases:** the README says some properties are read-only where the context disables them, naming custom colour spaces in an ACES workflow.
- **The method the README recommends:** set the wanted result from the UI and compare property snapshots before and after. Step 1 below is exactly that.

UNCONFIRMED, because no file on this Mac says it:

- The value strings for the colour spaces. `"DaVinci WG/Intermediate"` and `"Rec.709 Gamma 2.4"` are the names the UI shows, as this repo writes them (`lutlab/OWNER.md`, `lutlab/README.md`, the spec in #141, and a fake-backed test fixture). `"Rec.709 Gamma 2.4"` is also an Input Color Space value read from live clips (`detect.py`, `resolve_survey.py`), which suggests Resolve spells it that way, but that is a clip property and not a project setting. No Resolve file lists either as an API value for these keys. The `.pyi` gives only 'Rec.709' and 'Gamma 2.4' as examples.
- Whether the combined names go in `colorSpaceTimeline` and `colorSpaceOutput` alone, or the pair (`colorSpace...` plus `colorSpace...Gamma`) is needed, and what `separateColorSpaceAndGamma` does to that. The key name is in the `.pyi`; the label of the UI checkbox it matches is from memory.
- Which of `colorSpaceOutputToneMapping` and `outputDRT` the UI's Tone Mapping dropdown is. Both list the same six values.
- Whether any colour key can be written while `isAutoColorManage` is '1', and whether writing one key resets another (the Output DRT after the output colour space is the suspected case, noted in `colormgmt.py`).
- That `SetSetting` still works on 21.1.1.10 for these keys. The repo's own note (`resolve-mcp.md`, "Things that cost time to find") says even `colorScienceMode` has not been checked live on this build.
- That `GetSettings()` with no key returns on 21.1.1.10. `rpresolve/settingsguard.py` warns that a call that hangs inside Resolve cannot be caught, so this plan reads keys one at a time.
- `rpstills/README.md` says a stills timeline's `colorSpaceOutput` is set to `sRGB` with `SetSetting` and read back. No record of a live run is in the repo, so this is a repo claim and not evidence.

Web corroboration, not authority: a third-party MCP server's tool listing names `colorSpaceTimeline` and `colorSpaceOutput` as timeline setting names, and shows 'Rec.709' and 'Gamma 2.4' as example values. A Blackmagic forum thread ([viewtopic.php?p=1085893](https://forum.blackmagicdesign.com/viewtopic.php?p=1085893)) turned up in the search on setting a timeline colour space from a script; its content was not read. Neither confirms the DaVinci strings.

## What to try

The target state, from the Colour Pipeline of Record, on a fresh project:

| Setting | Key | Candidate value | Level |
|---|---|---|---|
| Colour science | `colorScienceMode` | `davinciYRGBColorManagedv2` | project (already shipped) |
| Automatic colour management off (prerequisite, if it reads '1') | `isAutoColorManage` | `0` | project |
| Timeline colour space | `colorSpaceTimeline` | `DaVinci WG/Intermediate` | project |
| Output colour space | `colorSpaceOutput` | `Rec.709 Gamma 2.4` | project |
| Tone mapping | `colorSpaceOutputToneMapping` | `None` | project |
| Gamut mapping | `colorSpaceOutputGamutMapping` | `None` | project |

Project level only. The spec's rows are Project Settings, and a timeline-level write needs `useCustomSettings` '1', which a forum thread cited in `settingsguard.py` reports changes colour management on its own. Timeline level gets one read-only probe at the end (step 6), not a write.

A fresh colour-managed project starts with its timeline and output at Rec.709 (Scene), per the spec, so that is the baseline Step 2 should read; its exact string is one of the things Step 2 records.

Alternate spellings, tried only if the candidate fails or reads back differently:

- **Split pair:** `separateColorSpaceAndGamma` '1', then `colorSpaceOutput` 'Rec.709' and `colorSpaceOutputGamma` 'Gamma 2.4'; for the timeline `colorSpaceTimeline` 'DaVinci WG/Intermediate' with `colorSpaceTimelineGamma` as Step 1 shows it.
- **Output DRT:** `outputDRT` 'None' in place of `colorSpaceOutputToneMapping`, if Step 1 shows the UI change moved that key.
- **Strings from the UI dump.** If Step 1 reads a string that differs from the candidates, use Step 1's string. It is what Resolve itself reports.
- **Both APIs:** every write once with `SetSetting(key, value)` (what the shipped tool uses) and once with `SetSettings({key: value})` (what the 21.1 README prefers). Record the return value of each.

## The run

Use a project made for this and deleted after. Name it so it cannot be mistaken for work (it must contain "Spike"). It needs no timeline and no clip, which is also the `set_color_management` fresh-project gate. Close any dialog before each write: a modal dialog makes a write report success and change nothing (the reason `api.set_setting_checked` reads back).

**Step 0. Make the project.** New project, nothing imported. Note the Resolve version (Help, About) in the results header. In Project Settings, Color Management, leave colour science at its default.

**Step 1. UI first, then read.** Set the target by hand, one dropdown at a time, in this order: Color science to DaVinci YRGB Color Managed, Automatic colour management off, timeline colour space DaVinci WG/Intermediate, output colour space Rec.709 Gamma 2.4, tone mapping None, gamut mapping None. After each change, paste the console script below with `MODE = "dump"` and fill Table 1. This gives the exact strings Resolve holds, which of the keys each dropdown moves, and what else moves with it. Last, run one more dump and paste the whole output under Table 1 as the hand-set end state; pass rule 7 compares against it after the project is gone.

**Step 2. Return to the default.** Delete the project and make a new one (Step 0) so the writes start from a fresh state. Do not dump it first; write mode prints the baseline itself, and that is the baseline Table 2 uses. Look at the Color Management panel only if you must, and change nothing in it.

**Step 3. Scripted writes, in order.** Run the script with `MODE = "write"` and `VARIANT = "A"`. It first reads the baseline key by key, then each row does the write, reads the same key back, and lists any other watched key that changed. After the last step it reads every written key again and prints a `FINAL` line for each, `PASS` or `DRIFT`; that is pass rule 2. Fill Table 2 from its output. Order matters: `colorScienceMode` first, then timeline, then output, then tone and gamut. If a step fails, note it and carry on, so every row has a result.

**Step 4. Writable, not just defaulted.** A key that reads 'None' before and after has proved nothing. The script's last four steps set tone and gamut mapping to a different value ('Simple', 'Saturation Mapping') and back to 'None'. Fill the Table 2 rows for them.

**Step 5. Restart read-back.** With the project from Step 3 still holding the target: save it (File, Save Project), close it to the Project Manager, reopen it, and run `MODE = "dump"`. Then quit Resolve, start it, open the project, dump again. Fill Table 3. A value that survives a close but not a quit, or the other way, is a fail.

**Step 6. Timeline probe (read only).** Make one empty timeline, run the dump with `TIMELINE = True` (it reads `Timeline.GetSetting` for the same keys), and note whether `useCustomSettings` reads '0' and the colour keys equal the project's. Do not set `useCustomSettings`. This is the last thing done in that project: it is no longer fresh, the script's write mode refuses it, and its dump needs `TIMELINE = True`. Step 7 starts a new project, which skips this probe; the timeline-level read is a one-project observation.

**Step 7. Repeat.** Delete the project. Make a second new project and repeat Steps 0, 3 and 5 only, with the order and strings that passed. Fill Table 4. One run can be luck; the rule below needs two.

**Step 8. Alternates, only if a candidate failed.** Run `VARIANT = "B"` (split pair) and `VARIANT = "C"` (the `SetSettings` dict form, all keys in one call), each on a new fresh project, and add their rows to Table 2 with the variant named.

### Console script

Paste into Workspace, Console, Python 3 tab. `resolve` is predefined in Resolve's console; the fallback import follows the 21.1 README. It refuses unless the project name contains "Spike" and holds no clip, and no timeline either unless it is a dump with `TIMELINE = True`. It writes only the listed keys and never saves, creates or loads a project.

```python
MODE = "dump"          # "dump" reads the watch list; "write" runs the variant
VARIANT = "A"          # "A" SetSetting; "B" split pair; "C" SetSettings calls
TIMELINE = False       # True: dump the current timeline's settings too (Step 6, dump only)
WATCH = ["colorScienceMode", "isAutoColorManage", "rcmPresetMode", "separateColorSpaceAndGamma",
         "colorSpaceTimeline", "colorSpaceTimelineGamma", "colorSpaceInput", "colorSpaceInputGamma",
         "colorSpaceOutput", "colorSpaceOutputGamma", "colorSpaceOutputToneMapping",
         "colorSpaceOutputGamutMapping", "inputDRT", "outputDRT", "useInverseDRT",
         "timelineWorkingLuminanceMode"]
# The .pyi puts the two colorSpaceInput keys on projects only and the gamut limit on timelines only.
TL_WATCH = [k for k in WATCH if not k.startswith("colorSpaceInput")] + ["colorSpaceOutputGamutLimit"]
S, M = "SetSetting", "SetSettings"
STEPS_A = [
    (S, "colorScienceMode", "davinciYRGBColorManagedv2"),
    (S, "isAutoColorManage", "0"),
    (S, "colorSpaceTimeline", "DaVinci WG/Intermediate"),
    (S, "colorSpaceOutput", "Rec.709 Gamma 2.4"),
    (S, "colorSpaceOutputToneMapping", "None"),
    (S, "colorSpaceOutputGamutMapping", "None"),
    (S, "colorSpaceOutputToneMapping", "Simple"), (S, "colorSpaceOutputToneMapping", "None"),
    (S, "colorSpaceOutputGamutMapping", "Saturation Mapping"), (S, "colorSpaceOutputGamutMapping", "None"),
]
STEPS_B = [   # split pair; fix the strings from Step 1 first
    (S, "colorScienceMode", "davinciYRGBColorManagedv2"),
    (S, "isAutoColorManage", "0"),
    (S, "separateColorSpaceAndGamma", "1"),
    (S, "colorSpaceTimeline", "DaVinci WG/Intermediate"),
    (S, "colorSpaceOutput", "Rec.709"), (S, "colorSpaceOutputGamma", "Gamma 2.4"),
    (S, "outputDRT", "None"), (S, "colorSpaceOutputGamutMapping", "None"),
    (S, "outputDRT", "Simple"), (S, "outputDRT", "None"),
    (S, "colorSpaceOutputGamutMapping", "Saturation Mapping"), (S, "colorSpaceOutputGamutMapping", "None"),
]
STEPS_C = [   # SetSettings; the second item is only a label, the dict is the whole call
    (M, "all six", {"colorScienceMode": "davinciYRGBColorManagedv2", "isAutoColorManage": "0",
                     "colorSpaceTimeline": "DaVinci WG/Intermediate", "colorSpaceOutput": "Rec.709 Gamma 2.4",
                     "colorSpaceOutputToneMapping": "None", "colorSpaceOutputGamutMapping": "None"}),
    (M, "tone and gamut away", {"colorSpaceOutputToneMapping": "Simple",
                                "colorSpaceOutputGamutMapping": "Saturation Mapping"}),
    (M, "tone and gamut back", {"colorSpaceOutputToneMapping": "None", "colorSpaceOutputGamutMapping": "None"}),
]
STEPS = {"A": STEPS_A, "B": STEPS_B, "C": STEPS_C}[VARIANT]

if "resolve" not in globals() or not hasattr(resolve, "GetVersion"):
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
p = resolve.GetProjectManager().GetCurrentProject()
assert p is not None, "REFUSED: no project is open"

def clips(folder):
    return len(folder.GetClipList() or []) + sum(clips(f) for f in folder.GetSubFolderList() or [])

def norm(v):
    return "" if v is None else str(v)   # like colormgmt.final_drift, except an int 0 stays '0'

def snap(obj, keys=None):
    out = {}
    print("reading:", end=" ")
    for k in keys or WATCH:
        print(k, end=" ", flush=True)    # the last name printed is the key that hung
        out[k] = obj.GetSetting(k)
    print()
    return out

assert "Spike" in p.GetName(), "REFUSED: the project name must contain 'Spike'"
if MODE == "write" or not TIMELINE:
    assert int(p.GetTimelineCount() or 0) == 0, "REFUSED: the project has a timeline"
assert clips(p.GetMediaPool().GetRootFolder()) == 0, "REFUSED: the project holds a clip"
print("Resolve", resolve.GetVersionString(), "| project", p.GetName())
if MODE == "dump":
    for k, v in snap(p).items():
        print(f"| {k} | {v!r} |")
    if TIMELINE:
        t = p.GetCurrentTimeline()
        print("timeline useCustomSettings:", t.GetSetting("useCustomSettings"))
        for k, v in snap(t, TL_WATCH).items():
            print(f"| timeline {k} | {v!r} |")
else:
    after = snap(p)
    print("baseline:", after)
    final = {}
    for api, key, val in STEPS:
        before = after
        wrote = {key: val} if api == S else val
        print("writing:", key, flush=True)
        try:
            ret = p.SetSetting(key, val) if api == S else p.SetSettings(val)
        except Exception as e:          # a rejected key is a result, not the end of the run
            print(f"| {VARIANT} | {api} | {key} | {val!r} | RAISED {type(e).__name__}: {e} | FAIL |")
            continue
        after = snap(p)
        if ret:
            final.update(wrote)         # FINAL only judges keys whose write was accepted
        bad = {k: after[k] for k, v in wrote.items() if norm(after[k]) != v}
        moved = {k: (before[k], after[k]) for k in WATCH if k not in wrote and before[k] != after[k]}
        read = {k: after[k] for k in wrote}
        ok = ret and not bad            # pass rule 1: the call returns True and the read-back matches
        print(f"| {VARIANT} | {api} | {key} | {val!r} | ret={ret} | read={read!r} | {'PASS' if ok else 'FAIL'} | moved={moved} |")
    after = snap(p)
    for k, v in final.items():
        print(f"FINAL | {k} | wanted {v!r} | now {after[k]!r} | {'PASS' if norm(after[k]) == v else 'DRIFT'}")
```

A write that raises (for example `AttributeError` if `SetSettings` does not exist on this build) is printed as `RAISED` and the run carries on; copy that line into Table 2. A key whose write was rejected is left out of the `FINAL` lines, so `DRIFT` means a key moved after an accepted write. Reads use the keyed `GetSetting`, which the 21.1 README lists as deprecated, not removed; `GetSettings()` is avoided for the hang risk above. If keyed reads fail outright on this build, say so in the results and rerun with `GetSettings()` on a throwaway project. If a call hangs the console, quit Resolve and treat it as a fail for that call. Every key name is printed just before it is read, and each write is announced as `writing: <key>`, so the last name printed is the call that hung.

## Results

Fill these in. Keep the tables in this file or paste them under the spike's dated result in the PR that acts on it.

**Header.** Date; Resolve version (the installed build is 21.1.1; the live checks recorded in the spec ran on 21.0.4.5, so say which this is); macOS; project name (throwaway, not a work project); "fresh" confirmed (no timeline, no clip).

**Table 1. Set by hand (Step 1).** One row per dropdown moved.

| UI change | Keys that changed | Exact value strings read | Anything else that moved |
|---|---|---|---|
| Colour science to DaVinci YRGB Color Managed | | | |
| Automatic colour management off | | | |
| Timeline colour space DaVinci WG/Intermediate | | | |
| Output colour space Rec.709 Gamma 2.4 | | | |
| Tone mapping None | | | |
| Gamut mapping None | | | |

**Table 2. Scripted writes (Steps 3, 4, 8).** One row per script line.

| Variant | API | Key | Value written | Return | Read back | Pass | Other keys moved |
|---|---|---|---|---|---|---|---|
| A | SetSetting | `colorScienceMode` | `davinciYRGBColorManagedv2` | | | | |
| A | SetSetting | `isAutoColorManage` | `0` | | | | |
| A | SetSetting | `colorSpaceTimeline` | `DaVinci WG/Intermediate` | | | | |
| A | SetSetting | `colorSpaceOutput` | `Rec.709 Gamma 2.4` | | | | |
| A | SetSetting | `colorSpaceOutputToneMapping` | `None` | | | | |
| A | SetSetting | `colorSpaceOutputGamutMapping` | `None` | | | | |
| A | SetSetting | tone mapping to `Simple` and back to `None` | | | | | |
| A | SetSetting | gamut mapping to `Saturation Mapping` and back to `None` | | | | | |
| B, C | | | | | | | |

Add one row per script line for variants B and C, with the variant letter in the first column. The script prints its `FINAL` lines last; paste them under the table.

**Table 3. Restart read-back (Step 5).**

| Key | After the scripted writes | After close and reopen | After quit and relaunch | UI shows the target |
|---|---|---|---|---|
| `colorScienceMode` | | | | |
| `colorSpaceTimeline` | | | | |
| `colorSpaceOutput` | | | | |
| `colorSpaceOutputToneMapping` | | | | |
| `colorSpaceOutputGamutMapping` | | | | |

**Table 4. Second fresh project (Step 7).** Same rows as Table 3, with the order that passed.

## Pass or fail

A key joins a preset only if every line holds for it:

1. `SetSetting` (or `SetSettings`, whichever the preset will use; name it) returns True and the same key reads back equal to the string written.
2. In the full run, in the order the preset will write, a final read of every preset key finds each equal to its wanted value. This is the `final_drift` check, so a key a later write resets fails here.
3. The values survive Step 5's close and quit.
4. The UI, looked at, shows the intended dropdown. A read-back string the UI does not reflect is a fail.
5. Steps 3 to 5 pass again on a second fresh project (Table 4).
6. For tone and gamut mapping, the key also changed to a different value and back (Step 4), so a pass is not just the default.
7. Compare end states, not paths. After Step 3, a full dump of the watch list matches Step 1's last dump (the hand-set result) for every key the variant did not write itself. A variant that writes a key Step 1 left alone (variant B and `separateColorSpaceAndGamma`) is compared on that key against what the variant intended, not against Step 1. Any other difference is named in the results before the key is accepted. Knock-on changes in a row's `moved` column are fine so long as the end states agree.

Outcomes:

- All keys pass: add them to a new preset (a second name in `PRESETS`, leaving `managed` as it is), in the order tested, with the exact strings Table 1 reported. Then update the tool description in `rpresolve/mcp/tools_write.py`, `resolve-mcp.md`, and the "Who sets it" column and the sentence under it in the spec's Colour Pipeline of Record. Record the Resolve version in `colormgmt.py` next to the keys.
- Some keys pass: add only those, each with its position in the order. The rest stay hand steps and the spec row says why.
- None pass, or the strings differ per project: the settings stay a hand step. Say so in the spec (it already does) and record the reason there, so nobody repeats the spike.
- Either way, a pass on this build is a pass on this build. Run it on the build the tool will drive, and re-run after a Resolve update before relying on it.

## The live sandbox test to add if keys pass

`production/tests/live_mcp_sandbox.py` cannot check this as it stands. It refuses unless the open project's name contains "Sandbox" and its id matches, and that project holds work, so `set_color_management` correctly refuses it (its check, "set_color_management refuses a project that holds work, or has nothing to set", accepts that refusal). A preset with new keys needs a second, fresh sandbox project. The sketch, unexecuted, as a separate entry point that reuses `Client` and `check` and skips the work-bearing snapshot:

```python
# Sketch only. Not in the repo. Run with the open project an EMPTY project whose name
# contains "Sandbox Fresh", after hand-checking it holds no timeline and no clip.
def check_colour_preset(c, P, project, preset):
    from rpresolve import colormgmt
    keys = colormgmt.PRESETS[preset]
    err, dry = c.tool("set_color_management", **P, preset=preset)
    check(not err and dry.get("would_change"), "dry run names the keys it would set")
    err, real = c.tool("set_color_management", **P, preset=preset,
                       dry_run=False, plan_sha=dry["plan_sha"])
    check(not err and real.get("exit_status") == 0 and not real.get("failed") and not real.get("drift"),
          "real run sets every key, reads each back, finds no drift")
    for key, wanted in keys:
        check(str(project.GetSetting(key)) == wanted, f"{key} reads '{wanted}' straight from Resolve")
    err, again = c.tool("set_color_management", **P, preset=preset)
    check(not err and not again.get("would_change"), "a second dry run has nothing left to set")
    # Then, by hand: save, quit, relaunch, reopen, and run this with --reread, which skips the
    # writes and only repeats the GetSetting loop above (a flag the sketch assumes the harness adds).
```

The result fields it reads (`would_change`, `plan_sha`, `exit_status`, `failed`, `drift`) are the ones `workflows.set_color_management` returns; the writes leave the project fresh, since they touch only settings, so the second dry run is not refused by the gate. Wire it as `--fresh-colour <preset>` with its own project name and id arguments, so one flag never runs against the work-bearing sandbox. The fake-backed tests in `tests/test_mcp_project_timeline.py` need the same new preset exercised, as they do for `managed`.
