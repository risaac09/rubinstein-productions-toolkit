# Spike plan: can the scripting API set the colour pipeline of record?

*Drafted 2026-10-16 (Amendment 2, item 16 follow-up). A plan, not a result. Nothing here has been run, and no preset key has been added.*

You run this in Resolve, in a throwaway project. The question: can `set_color_management` take more than `colorScienceMode`, so that a fresh colour-managed project arrives with the timeline colour space, output colour space and tone and gamut mapping of the Colour Pipeline of Record ([resolve-template-spec.md](../resolve-template-spec.md)) instead of a hand step?

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

- The value strings for the colour spaces. `"DaVinci WG/Intermediate"` and `"Rec.709 Gamma 2.4"` appear in this repo's spec, lutlab notes and one test fixture, as the names the UI shows. No Resolve file lists them as API values. The `.pyi` gives only 'Rec.709' and 'Gamma 2.4' as examples.
- Whether the combined names go in `colorSpaceTimeline` and `colorSpaceOutput` alone, or the pair (`colorSpace...` plus `colorSpace...Gamma`) is needed, and what `separateColorSpaceAndGamma` does to that. That checkbox is named from memory of the UI.
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
| Timeline colour space | `colorSpaceTimeline` | `DaVinci WG/Intermediate` | project |
| Output colour space | `colorSpaceOutput` | `Rec.709 Gamma 2.4` | project |
| Tone mapping | `colorSpaceOutputToneMapping` | `None` | project |
| Gamut mapping | `colorSpaceOutputGamutMapping` | `None` | project |

Project level only. The spec's rows are Project Settings, and a timeline-level write needs `useCustomSettings` '1', which a forum thread cited in `settingsguard.py` reports changes colour management on its own. Timeline level gets one read-only probe at the end (step 6), not a write.

Alternate spellings, tried only if the candidate fails or reads back differently:

- **Split pair:** `separateColorSpaceAndGamma` '1', then `colorSpaceOutput` 'Rec.709' and `colorSpaceOutputGamma` 'Gamma 2.4'; for the timeline `colorSpaceTimeline` 'DaVinci WG/Intermediate' with `colorSpaceTimelineGamma` as Step 1 shows it.
- **Output DRT:** `outputDRT` 'None' in place of `colorSpaceOutputToneMapping`, if Step 1 shows the UI change moved that key.
- **Strings from the UI dump.** If Step 1 reads a string that differs from the candidates, use Step 1's string. It is what Resolve itself reports.
- **Both APIs:** every write once with `SetSetting(key, value)` (what the shipped tool uses) and once with `SetSettings({key: value})` (what the 21.1 README prefers). Record the return value of each.

## The run

Use a project made for this and deleted after. Name it so it cannot be mistaken for work (it must contain "Spike"). It needs no timeline and no clip, which is also the `set_color_management` fresh-project gate. Close any dialog before each write: a modal dialog makes a write report success and change nothing (the reason `api.set_setting_checked` reads back).

**Step 0. Make the project.** New project, nothing imported. Note the Resolve version (Help, About) in the results header. In Project Settings, Color Management, leave colour science at its default.

**Step 1. UI first, then read.** Set the target by hand, one dropdown at a time, in this order: Color science to DaVinci YRGB Color Managed, Automatic colour management off, timeline colour space DaVinci WG/Intermediate, output colour space Rec.709 Gamma 2.4, tone mapping None, gamut mapping None. After each change, paste the console script below with `MODE = "dump"` and fill Table 1. This gives the exact strings Resolve holds, which of the keys each dropdown moves, and what else moves with it.

**Step 2. Return to the default.** Delete the project and make a new one (Step 0) so the writes start from a fresh state. Run the script with `MODE = "dump"` once and keep the output as the baseline.

**Step 3. Scripted writes, in order.** Run the script with `MODE = "write"` and `STEPS = STEPS_A`. Each row does the write, reads the same key back, and lists any other watched key that changed. Fill Table 2 from its output. Order matters: `colorScienceMode` first, then timeline, then output, then tone and gamut. If a step fails, note it and carry on, so every row has a result.

**Step 4. Writable, not just defaulted.** A key that reads 'None' before and after has proved nothing. The script's last four steps set tone and gamut mapping to a different value ('Simple', 'Saturation Mapping') and back to 'None'. Fill the Table 2 rows for them.

**Step 5. Restart read-back.** With the project from Step 3 still holding the target: save it (File, Save Project), close it to the Project Manager, reopen it, and run `MODE = "dump"`. Then quit Resolve, start it, open the project, dump again. Fill Table 3. A value that survives a close but not a quit, or the other way, is a fail.

**Step 6. Timeline probe (read only).** Make one empty timeline, run the dump with `TIMELINE = True` (it reads `Timeline.GetSetting` for the same keys), and note whether `useCustomSettings` reads '0' and the colour keys equal the project's. This ends the project's freshness, so it is last. Do not set `useCustomSettings`.

**Step 7. Repeat.** Delete the project. Make a second new project and repeat Steps 0, 3 and 5 only, with the order and strings that passed. Fill Table 4. One run can be luck; the rule below needs two.

**Step 8. Alternates, only if a candidate failed.** Run `STEPS_B` (split pair) and `STEPS_C` (the `SetSettings` dict form, all keys in one call) on fresh projects, each from a new project, and add their rows to Table 2 with the variant named.

### Console script

Paste into Workspace, Console, Python 3 tab. `resolve` is predefined in Resolve's console; the fallback import follows the 21.1 README. It refuses unless the project name contains "Spike" and holds no timeline (outside `TIMELINE` mode) and no clip. It writes only the listed keys and never saves, creates or loads a project.

```python
MODE = "dump"          # "dump" reads the watch list; "write" runs STEPS
TIMELINE = False       # True: also read the current timeline's settings (Step 6)
WATCH = ["colorScienceMode", "isAutoColorManage", "rcmPresetMode", "separateColorSpaceAndGamma",
         "colorSpaceTimeline", "colorSpaceTimelineGamma", "colorSpaceInput", "colorSpaceInputGamma",
         "colorSpaceOutput", "colorSpaceOutputGamma", "colorSpaceOutputToneMapping",
         "colorSpaceOutputGamutMapping", "inputDRT", "outputDRT", "useInverseDRT",
         "timelineWorkingLuminanceMode", "colorSpaceOutputGamutLimit"]
S, M = "SetSetting", "SetSettings"
STEPS_A = [
    (S, "colorScienceMode", "davinciYRGBColorManagedv2"),
    (S, "colorSpaceTimeline", "DaVinci WG/Intermediate"),
    (S, "colorSpaceOutput", "Rec.709 Gamma 2.4"),
    (S, "colorSpaceOutputToneMapping", "None"),
    (S, "colorSpaceOutputGamutMapping", "None"),
    (S, "colorSpaceOutputToneMapping", "Simple"), (S, "colorSpaceOutputToneMapping", "None"),
    (S, "colorSpaceOutputGamutMapping", "Saturation Mapping"), (S, "colorSpaceOutputGamutMapping", "None"),
]
STEPS_B = [   # split pair; fix the strings from Step 1 first
    (S, "colorScienceMode", "davinciYRGBColorManagedv2"),
    (S, "separateColorSpaceAndGamma", "1"),
    (S, "colorSpaceTimeline", "DaVinci WG/Intermediate"),
    (S, "colorSpaceOutput", "Rec.709"), (S, "colorSpaceOutputGamma", "Gamma 2.4"),
    (S, "outputDRT", "None"), (S, "colorSpaceOutputGamutMapping", "None"),
]
STEPS_C = [   # one SetSettings call; the printed row is the whole call
    (M, "colorScienceMode+timeline+output+tone+gamut",
     {"colorScienceMode": "davinciYRGBColorManagedv2", "colorSpaceTimeline": "DaVinci WG/Intermediate",
      "colorSpaceOutput": "Rec.709 Gamma 2.4", "colorSpaceOutputToneMapping": "None",
      "colorSpaceOutputGamutMapping": "None"}),
]
STEPS = STEPS_A

if "resolve" not in globals() or not hasattr(resolve, "GetVersion"):
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
p = resolve.GetProjectManager().GetCurrentProject()

def clips(folder):
    return len(folder.GetClipList() or []) + sum(clips(f) for f in folder.GetSubFolderList() or [])

def snap(obj):
    return {k: obj.GetSetting(k) for k in WATCH}

assert "Spike" in p.GetName(), "REFUSED: the project name must contain 'Spike'"
if not TIMELINE:
    assert int(p.GetTimelineCount() or 0) == 0, "REFUSED: the project has a timeline"
assert clips(p.GetMediaPool().GetRootFolder()) == 0, "REFUSED: the project holds a clip"
print("Resolve", resolve.GetVersionString(), "| project", p.GetName())
if MODE == "dump":
    for k, v in snap(p).items():
        print(f"| {k} | {v!r} |")
    if TIMELINE:
        t = p.GetCurrentTimeline()
        print("timeline useCustomSettings:", t.GetSetting("useCustomSettings"))
        for k, v in snap(t).items():
            print(f"| timeline {k} | {v!r} |")
else:
    for api, key, val in STEPS:
        before = snap(p)
        ret = p.SetSetting(key, val) if api == S else p.SetSettings(val)
        after = snap(p)
        got = after.get(key) if api == S else {k: after.get(k) for k in val}
        ok = got == val
        wrote = {key} if api == S else set(val)
        moved = {k: (before[k], after[k]) for k in WATCH if k not in wrote and before[k] != after[k]}
        print(f"| {api} | {key} | {val!r} | ret={ret} | read={got!r} | {'PASS' if ok else 'FAIL'} | moved={moved} |")
```

If `p.SetSettings` does not exist on this build the script raises `AttributeError` on a `STEPS_C` run; record that in Table 2 as "no SetSettings on 21.1.1.10". If a dump or write call hangs the console, quit Resolve, note which key was read last, and treat it as a fail for that call.

## Results

Fill these in. Keep the tables in this file or paste them under the spike's dated result in the PR that acts on it.

**Header.** Date; Resolve version; macOS; project name (throwaway, not a work project); "fresh" confirmed (no timeline, no clip).

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
| A | SetSetting | `colorSpaceTimeline` | `DaVinci WG/Intermediate` | | | | |
| A | SetSetting | `colorSpaceOutput` | `Rec.709 Gamma 2.4` | | | | |
| A | SetSetting | `colorSpaceOutputToneMapping` | `None` | | | | |
| A | SetSetting | `colorSpaceOutputGamutMapping` | `None` | | | | |
| A | SetSetting | tone mapping to `Simple` and back to `None` | | | | | |
| A | SetSetting | gamut mapping to `Saturation Mapping` and back to `None` | | | | | |
| B or C | | | | | | | |

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
7. No watched key moves apart from the one written and those Table 1 already showed moving with it. An unexpected mover is a fail until it is understood.

Outcomes:

- All keys pass: add them to a new preset (a second name in `PRESETS`, leaving `managed` as it is), in the order tested, with the exact strings Table 1 reported. Then update the tool description in `rpresolve/mcp/tools_write.py`, `resolve-mcp.md`, and the "Who sets it" column and the sentence under it in the spec's Colour Pipeline of Record. Record the Resolve version in `colormgmt.py` next to the keys.
- Some keys pass: add only those, each with its position in the order. The rest stay hand steps and the spec row says why.
- None pass, or the strings differ per project: the settings stay a hand step. Say so in the spec (it already does) and record the reason there, so nobody repeats the spike.
- Either way, a pass on this build is a pass on this build. Re-run after a Resolve update before relying on it.

## The live sandbox test to add if keys pass

`production/tests/live_mcp_sandbox.py` cannot check this as it stands. It refuses unless the open project's name contains "Sandbox" and its id matches, and that project holds work, so `set_color_management` correctly refuses it (the existing check accepts that refusal). A preset with new keys needs a second, fresh sandbox project. The sketch, unexecuted, as a separate entry point that reuses `Client` and `check` and skips the work-bearing snapshot:

```python
# Sketch only. Not in the repo. Run with the open project an EMPTY project whose name
# contains "Sandbox Fresh", after hand-checking it holds no timeline and no clip.
def check_colour_preset(c, P, project, preset):
    from rpresolve import colormgmt
    keys = colormgmt.PRESETS[preset]
    err, dry = c.tool("set_color_management", **P, preset=preset)
    check(not err and dry["would_change"], "dry run names the keys it would set")
    err, real = c.tool("set_color_management", **P, preset=preset,
                       dry_run=False, plan_sha=dry["plan_sha"])
    check(not err and real["exit_status"] == 0 and not real["failed"] and not real["drift"],
          "real run sets every key, reads each back, finds no drift")
    for key, wanted in keys:
        check(str(project.GetSetting(key)) == wanted, f"{key} reads '{wanted}' straight from Resolve")
    err, again = c.tool("set_color_management", **P, preset=preset)
    check(not err and again["would_change"] == [], "a second dry run has nothing left to set")
    # Then, by hand: save, quit, relaunch, reopen, and run this with --reread, which skips the
    # writes and only repeats the GetSetting loop above.
```

Wire it as `--fresh-colour <preset>` with its own project name and id arguments, so one flag never runs against the work-bearing sandbox. The fake-backed tests in `tests/test_mcp_project_timeline.py` need the same new preset exercised, as they do for `managed`.
