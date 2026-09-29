"""
Offline tests for rpresolve.deliver and the delivery keys in
rpresolve.config: the naming rule both ways, destination defaults and a
config overlay, the checks before a render is queued (target folder,
unmounted share, git tree, existing file or sidecar, captions without a
subtitle track), and the Deliver settings a destination sets. Synthetic
names and temporary folders only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PRODUCTION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PRODUCTION))

from rpresolve import config as rpconfig, deliver  # noqa: E402

CLIP = dict(show="SW", episode=1, guest="Guest", index=1, slug="example-clip")


def dest(key, **over):
    d = deliver.destination(rpconfig.load_config("/nonexistent/config.json"), key)
    d.update(over)
    return d


class TestNames(unittest.TestCase):
    def test_builds_the_example(self):
        self.assertEqual(deliver.deliverable_name("SW", 1, "Guest", 1, "example-clip", "16x9",
                                                  "mp4"),
                         "SW001_Guest_01_example-clip_16x9.mp4")
        self.assertEqual(deliver.deliverable_name("AB", "042", "G2", "12", "a-b-c", "9x16",
                                                  ".mp4"),
                         "AB042_G2_12_a-b-c_9x16.mp4")
        self.assertEqual(deliver.master_name("Client", "example-slug"),
                         "Client_example-slug_master.mov")

    def test_each_part_is_validated(self):
        bad = [dict(show="sw"), dict(show="S1"), dict(show=""), dict(episode=1000),
               dict(episode=-1), dict(episode="1a"), dict(episode=True), dict(guest="Two Words"),
               dict(guest="Guest_1"), dict(guest="Gäst"), dict(index=0), dict(index=100),
               dict(slug="Example"), dict(slug="example_clip"), dict(slug="-lead"),
               dict(slug="trail-"), dict(slug="dou--ble"), dict(slug="a" * 61),
               dict(episode="００１"), dict(index="٠١")]
        for over in bad:
            with self.subTest(over=over):
                with self.assertRaises(deliver.NameRuleError):
                    p = {**CLIP, **over}
                    deliver.deliverable_name(p["show"], p["episode"], p["guest"], p["index"],
                                             p["slug"], "16x9", "mp4")
        with self.assertRaisesRegex(deliver.NameRuleError, "aspect"):
            deliver.deliverable_name("SW", 1, "G", 1, "s", "4x5", "mp4")
        with self.assertRaisesRegex(deliver.NameRuleError, "extension"):
            deliver.deliverable_name("SW", 1, "G", 1, "s", "16x9", "mkv")
        with self.assertRaisesRegex(deliver.NameRuleError, "client"):
            deliver.master_name("Client Name", "s")
        with self.assertRaisesRegex(deliver.NameRuleError, "mov"):
            deliver.master_name("Client", "s", "mp4")

    def test_parse_round_trips(self):
        name = "SW001_Guest_01_example-clip_16x9.mp4"
        p = deliver.parse_name(name)
        self.assertEqual(p, {"kind": "clip", "show": "SW", "episode": 1, "guest": "Guest",
                             "index": 1, "slug": "example-clip", "aspect": "16x9",
                             "ext": "mp4"})
        self.assertEqual(deliver.deliverable_name(p["show"], p["episode"], p["guest"],
                                                  p["index"], p["slug"], p["aspect"], p["ext"]),
                         name)
        self.assertEqual(deliver.parse_name("/any/dir/Client_example-slug_master.mov"),
                         {"kind": "master", "client": "Client", "slug": "example-slug",
                          "ext": "mov"})

    def test_parse_says_what_is_wrong(self):
        cases = {
            "SW001_Guest_01_example-clip_16x9.mkv": "extension",
            "SW001_Guest_01_example-clip_16x9": "no extension",
            "SW01_Guest_01_example-clip_16x9.mp4": "3-digit episode",
            "sw001_Guest_01_example-clip_16x9.mp4": "show code",
            "SW001_Guest Name_01_example-clip_16x9.mp4": "guest",
            "SW001_Guest_1_example-clip_16x9.mp4": "index",
            "SW001_Guest_00_example-clip_16x9.mp4": "01 to 99",
            "SW001_Guest_01_Example-Clip_16x9.mp4": "slug",
            "SW001_Guest_01_example-clip_4x5.mp4": "aspect",
            "SW001_Guest_01_example_clip_16x9.mp4": "five parts",
            "Client_example-slug_master.mp4": ".mov",
            "Client Co_example-slug_master.mov": "client",
            "Client_Slug_master.mov": "slug",
            # digits are ASCII 0-9 only: fullwidth and Arabic-Indic digits break the rule
            "SW００１_Guest_０１_example-clip_16x9.mp4": "3-digit episode",
            "SW١٢٣_Guest_01_example-clip_16x9.mp4": "3-digit episode",
            "SW001_Guest_٠١_example-clip_16x9.mp4": "index",
        }
        for name, why in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(deliver.NameRuleError, why):
                    deliver.parse_name(name)

    def test_name_for_a_destination(self):
        self.assertEqual(deliver.name_for(dest("linkedin_9x16"), CLIP),
                         "SW001_Guest_01_example-clip_9x16.mp4")
        self.assertEqual(deliver.name_for(dest("client_master"),
                                          {"client": "Client", "slug": "example-slug"}),
                         "Client_example-slug_master.mov")
        with self.assertRaisesRegex(deliver.NameRuleError, "missing guest"):
            deliver.name_for(dest("youtube_16x9"), {**CLIP, "guest": ""})
        with self.assertRaisesRegex(deliver.NameRuleError, "not used: client"):
            deliver.name_for(dest("youtube_16x9"), {**CLIP, "client": "X"})

    def test_name_problems_against_a_destination(self):
        yt = dest("youtube_16x9")
        self.assertEqual(deliver.name_problems("SW001_Guest_01_example-clip_16x9.mp4", yt), [])
        self.assertIn("aspect 9x16",
                      deliver.name_problems("SW001_Guest_01_example-clip_9x16.mp4", yt)[0])
        self.assertIn("master name",
                      deliver.name_problems("Client_example-slug_master.mov", yt)[0])
        self.assertIn("clip name", deliver.name_problems(
            "SW001_Guest_01_example-clip_16x9.mov", dest("client_master"))[0])


class TestConfig(unittest.TestCase):
    def test_defaults_cover_every_destination(self):
        cfg = rpconfig.load_config("/nonexistent/config.json")
        self.assertEqual(deliver.destination_keys(cfg), sorted([
            "client_master", "linkedin_16x9", "linkedin_1x1", "linkedin_9x16",
            "substack_16x9", "youtube_16x9", "youtube_16x9_hd"]))
        yt = deliver.destination(cfg, "youtube_16x9")
        self.assertEqual((yt["format"], yt["codec"], yt["resolution"]),
                         ("mp4", "H265", {"width": 3840, "height": 2160}))
        self.assertEqual(deliver.destination(cfg, "youtube_16x9_hd")["resolution"],
                         {"width": 1920, "height": 1080})
        self.assertEqual((yt["loudness"]["integrated_lufs"], yt["loudness"]["tolerance_lu"],
                          yt["loudness"]["true_peak_max_dbtp"]), (-14.0, 0.5, -1.0))
        self.assertEqual((yt["audio"]["codec"], yt["audio"]["sample_rate"],
                          yt["audio"]["channels"]), ("aac", 48000, 2))
        self.assertEqual(yt["captions"], "sidecar")
        self.assertEqual(yt["data_burn_in"], "None")
        self.assertEqual(yt["color"]["resolve"], {"ColorSpaceTag": "Rec.709",
                                                  "GammaTag": "Gamma 2.4"})
        self.assertEqual(yt["color"]["expect"], {"color_primaries": "bt709",
                                                 "color_space": "bt709",
                                                 "color_transfer": None,
                                                 "color_range": "tv"})
        for key, size, captions in (("linkedin_16x9", (1920, 1080), "sidecar"),
                                    ("linkedin_9x16", (1080, 1920), "burnin"),
                                    ("linkedin_1x1", (1080, 1080), "burnin"),
                                    ("substack_16x9", (1920, 1080), "sidecar")):
            d = deliver.destination(cfg, key)
            self.assertEqual((d["codec"], d["format"], d["loudness"]["integrated_lufs"],
                              (d["resolution"]["width"], d["resolution"]["height"]),
                              d["captions"]), ("H264", "mp4", -16.0, size, captions))
        m = deliver.destination(cfg, "client_master")
        self.assertEqual((m["format"], m["codec"], m["resolution"], m["captions"]),
                         ("mov", "ProRes422HQ", "timeline", "none"))
        self.assertEqual((m["audio"]["codec"], m["audio"]["bit_depth"],
                          m["audio"]["sample_rate"]), ("lpcm", 24, 48000))
        self.assertFalse(deliver.has_loudness_target(m))

    def test_the_repo_config_matches_the_built_in_defaults(self):
        with open(PRODUCTION / "resolve-config.json", encoding="utf-8") as f:
            repo = json.load(f)
        for key in ("deliver", "destinations"):
            self.assertEqual(repo[key], rpconfig.DEFAULT_CONFIG[key], key)
        self.assertNotIn("/Volumes", json.dumps(repo))

    def test_an_overlay_changes_one_field_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "overlay.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"deliver": {"loudness": {"tolerance_lu": 1.0}},
                           "destinations": {
                               "youtube_16x9": {"target_dir": "/elsewhere/out",
                                                "loudness": {"integrated_lufs": -13}},
                               "linkedin_1x1": None,
                               "vimeo_16x9": {**rpconfig.DEFAULT_CONFIG["destinations"][
                                   "substack_16x9"], "name": "Vimeo"}}}, f)
            cfg = rpconfig.load_config(p)
        yt = deliver.destination(cfg, "youtube_16x9")
        self.assertEqual(yt["target_dir"], "/elsewhere/out")
        self.assertEqual(yt["loudness"]["integrated_lufs"], -13)
        self.assertEqual(yt["loudness"]["tolerance_lu"], 1.0)  # house rule overlaid
        self.assertEqual(yt["loudness"]["true_peak_max_dbtp"], -1.0)  # kept
        self.assertEqual(yt["codec"], "H265")  # kept
        self.assertEqual(deliver.destination(cfg, "vimeo_16x9")["name"], "Vimeo")
        self.assertNotIn("linkedin_1x1", deliver.destination_keys(cfg))
        with self.assertRaisesRegex(deliver.DeliverError, "unknown destination 'linkedin_1x1'"):
            deliver.destination(cfg, "linkedin_1x1")
        # render presets still replace as before
        self.assertEqual(sorted(cfg["render_presets"]), ["linkedin", "master", "story",
                                                         "youtube"])

    def test_a_named_overlay_that_cannot_be_read_is_an_error_when_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = {"trailing-comma.json": '{"destinations": {"linkedin_16x9": {"resolution": '
                                          '{"width": 64, "height": 36}},}}',
                   "a-list.json": "[1, 2]", "empty.json": "",
                   "latin1.json": b'{"x": "caf\xe9"}'}
            for name, text in bad.items():
                p = os.path.join(tmp, name)
                with open(p, "wb") as f:
                    f.write(text if isinstance(text, bytes) else text.encode("utf-8"))
                with self.subTest(name=name):
                    with self.assertRaisesRegex(rpconfig.ConfigError, "could not read"):
                        rpconfig.load_config(p, strict=True)
                    warned = []
                    cfg = rpconfig.load_config(p, warn=warned.append)  # the old, lenient way
                    self.assertEqual(len(warned), 1)
                    self.assertEqual(cfg["destinations"]["linkedin_16x9"]["resolution"],
                                     {"width": 1920, "height": 1080})
            with self.assertRaisesRegex(rpconfig.ConfigError, "not found"):
                rpconfig.load_config(os.path.join(tmp, "missing.json"), strict=True)
            self.assertTrue(issubclass(rpconfig.ConfigError, ValueError))

    def test_an_overlay_lies_over_the_repo_config_not_the_built_in_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = json.loads(json.dumps(rpconfig.DEFAULT_CONFIG))
            repo["destinations"]["linkedin_16x9"]["loudness"]["integrated_lufs"] = -18.0
            repo["deliver"]["loudness"]["tolerance_lu"] = 1.0
            repo["default_framerate"] = "25"
            repo_path = os.path.join(tmp, "resolve-config.json")
            with open(repo_path, "w", encoding="utf-8") as f:
                json.dump(repo, f)
            overlay = os.path.join(tmp, "overlay.json")
            with open(overlay, "w", encoding="utf-8") as f:
                json.dump({"destinations": {"linkedin_16x9": {"target_dir": "/elsewhere/out"},
                                            "youtube_16x9": {"loudness": {
                                                "integrated_lufs": -13.0}}}}, f)
            with mock.patch.object(rpconfig, "CONFIG_PATH_DEFAULT", Path(repo_path)):
                alone = rpconfig.load_config()
                laid = rpconfig.load_config(overlay)
                strict = rpconfig.load_config(overlay, strict=True)
                with mock.patch.dict(os.environ, {"RPRESOLVE_CONFIG": overlay}):
                    from rpresolve.mcp import tools_offline
                    mcp = tools_offline.deliver_config()
        for cfg in (laid, strict, mcp):
            li = deliver.destination(cfg, "linkedin_16x9")
            self.assertEqual(li["loudness"]["integrated_lufs"], -18.0)  # the repo edit holds
            self.assertEqual(li["loudness"]["tolerance_lu"], 1.0)
            self.assertEqual(li["target_dir"], "/elsewhere/out")  # the overlay adds to it
            self.assertEqual(deliver.destination(cfg, "youtube_16x9")["loudness"]
                             ["integrated_lufs"], -13.0)
            # keys other than the delivery ones keep their rule: a named file replaces the
            # defaults for them, and this overlay names none
            self.assertEqual(cfg["default_framerate"], "23.976")
        self.assertEqual(deliver.destination(alone, "linkedin_16x9")["loudness"]
                         ["integrated_lufs"], -18.0)
        self.assertEqual(alone["default_framerate"], "25")

    def test_merge_leaves_its_inputs_alone(self):
        a = {"x": {"y": 1, "z": [1]}}
        b = {"x": {"y": 2}}
        self.assertEqual(rpconfig.merge(a, b), {"x": {"y": 2, "z": [1]}})
        self.assertEqual(a, {"x": {"y": 1, "z": [1]}})

    def test_a_broken_destination_is_refused_with_reasons(self):
        cfg = rpconfig.load_config("/nonexistent/config.json")
        cfg["destinations"]["youtube_16x9"].update(
            {"format": "mkv", "captions": "subs", "resolution": {"width": 0, "height": 1},
             "target_dir": "relative/dir"})
        with self.assertRaises(deliver.DeliverError) as cm:
            deliver.destination(cfg, "youtube_16x9")
        for word in ("format", "captions", "resolution", "target_dir"):
            self.assertIn(word, str(cm.exception))


class TestOutputProblems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.name = "SW001_Guest_01_example-clip_16x9.mp4"
        self.yt = dest("youtube_16x9")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_clean_folder_passes(self):
        self.assertEqual(deliver.output_problems(self.dir, self.name, self.yt), [])

    def test_missing_or_relative_folder(self):
        self.assertIn("does not exist", deliver.output_problems(
            os.path.join(self.dir, "nope"), self.name, self.yt)[0])
        self.assertIn("absolute", deliver.output_problems("relative/out", self.name,
                                                          self.yt)[0])

    def test_a_dropped_share_left_on_the_boot_disk_is_refused(self):
        volumes = os.path.join(self.dir, "Volumes")
        leftover = os.path.join(volumes, "Work", "Active", "renders")
        os.makedirs(leftover)
        problems = deliver.output_problems(leftover, self.name, self.yt, volumes_root=volumes)
        self.assertEqual(len(problems), 1)
        self.assertIn("not mounted", problems[0])
        self.assertEqual(deliver.volume_root(leftover, volumes), os.path.join(volumes, "Work"))
        with mock.patch.object(deliver.os.path, "ismount", return_value=True):
            self.assertEqual(deliver.output_problems(leftover, self.name, self.yt,
                                                     volumes_root=volumes), [])
        self.assertIsNone(deliver.volume_root(self.dir, volumes))

    def test_a_dropped_share_is_refused_under_any_spelling(self):
        volumes = os.path.join(self.dir, "Volumes")
        os.makedirs(os.path.join(volumes, "Work", "Active"))
        lower = os.path.join(self.dir, "volumes", "work", "active")
        firm = "/System/Volumes/Data" + os.path.join(volumes, "Work", "Active")
        spellings = [lower, firm]
        if not os.path.isdir(lower):
            self.skipTest("this folder is on a case-sensitive file system")
        for target in spellings:
            with self.subTest(target=target):
                if not os.path.isdir(target):
                    continue  # no Data-volume firmlink on this system
                problems = deliver.output_problems(target, self.name, self.yt,
                                                   volumes_root=volumes)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("not mounted", problems[0])
                with mock.patch.object(deliver.os.path, "ismount", return_value=True):
                    self.assertEqual(deliver.output_problems(target, self.name, self.yt,
                                                             volumes_root=volumes), [])
        self.assertEqual(deliver.volume_root(lower, volumes),
                         os.path.join(self.dir, "volumes", "work"))
        if os.path.isdir(firm):
            self.assertEqual(deliver.volume_root(firm, volumes),
                             "/System/Volumes/Data" + os.path.join(volumes, "Work"))

    def test_volume_root_of_the_real_volumes_folder(self):
        # a share name that does not exist, so nothing on a real share is read
        for path in ("/Volumes/NoSuchShare-rpresolve/a", "/volumes/NoSuchShare-rpresolve/a",
                     "/System/Volumes/Data/Volumes/NoSuchShare-rpresolve/a"):
            with self.subTest(path=path):
                self.assertEqual(deliver.volume_root(path).casefold(),
                                 path[:-2].casefold())
        self.assertIsNone(deliver.volume_root("/Users/someone/renders"))
        self.assertIsNone(deliver.volume_root("/Volumes"))

    def test_a_queued_job_under_another_spelling_is_a_clash(self):
        out = os.path.join(self.dir, self.name)
        variants = [os.path.join(self.dir, self.name.upper()),
                    "/System/Volumes/Data" + out]
        if os.path.isdir(self.dir.upper()):
            variants.append(os.path.join(self.dir.upper(), self.name))
        for queued in variants:
            with self.subTest(queued=queued):
                problems = deliver.output_problems(self.dir, self.name, self.yt,
                                                   queued={queued})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("render queue", problems[0])
        self.assertEqual(deliver.output_problems(
            self.dir, self.name, self.yt,
            queued={os.path.join(self.dir, "SW001_Guest_02_example-clip_16x9.mp4"),
                    os.path.join(self.dir, "gone", self.name)}), [])

    def test_inside_a_git_tree_is_refused(self):
        repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        os.makedirs(os.path.join(repo, "out"))
        problems = deliver.output_problems(os.path.join(repo, "out"), self.name, self.yt)
        self.assertTrue(any("git working tree" in p for p in problems))

    def test_existing_file_sidecar_or_queued_job_is_refused(self):
        out = os.path.join(self.dir, self.name)
        open(out, "w").close()
        self.assertIn("already exists", deliver.output_problems(self.dir, self.name,
                                                                self.yt)[0])
        os.remove(out)
        open(out[:-4] + ".srt", "w").close()
        self.assertIn("caption file", deliver.output_problems(self.dir, self.name, self.yt)[0])
        os.remove(out[:-4] + ".srt")
        # a burn-in destination makes no sidecar, so an .srt with its own stem beside it is
        # no clash (deliver-check reports that file later)
        vertical = "SW001_Guest_01_example-clip_9x16"
        open(os.path.join(self.dir, vertical + ".srt"), "w").close()
        self.assertEqual(deliver.output_problems(self.dir, vertical + ".mp4",
                                                 dest("linkedin_9x16")), [])
        self.assertIn("caption file", deliver.output_problems(
            self.dir, vertical + ".mp4", dest("linkedin_9x16", captions="sidecar"))[0])
        os.remove(os.path.join(self.dir, vertical + ".srt"))
        self.assertIn("render queue", deliver.output_problems(self.dir, self.name, self.yt,
                                                              queued={out})[0])

    def test_any_caption_file_for_the_stem_blocks_a_sidecar_job(self):
        stem = os.path.join(self.dir, self.name[:-4])
        for tail in (".vtt", ".SRT", ".en.srt", ".scc", ".ttml", ".xml", "_Subtitle 1.srt"):
            with self.subTest(tail=tail):
                open(stem + tail, "w").close()
                try:
                    problems = deliver.output_problems(self.dir, self.name, self.yt)
                    self.assertEqual(len(problems), 1, problems)
                    self.assertIn("caption file", problems[0])
                    self.assertIn(os.path.basename(stem + tail), problems[0])
                finally:
                    os.remove(stem + tail)
        os.symlink(os.path.join(self.dir, "gone.srt"), stem + ".srt")  # dangling
        self.assertIn("caption file", deliver.output_problems(self.dir, self.name, self.yt)[0])
        os.remove(stem + ".srt")
        for other in (stem + ".txt", stem + ".mp4.part", os.path.join(self.dir, "other.srt")):
            open(other, "w").close()
        self.assertEqual(deliver.output_problems(self.dir, self.name, self.yt), [])

    def test_caption_files(self):
        stem = os.path.join(self.dir, self.name[:-4])
        for tail in (".en.srt", ".VTT", ".mp4", ".txt"):
            open(stem + tail, "w").close()
        os.symlink(os.path.join(self.dir, "gone"), stem + ".srt")
        self.assertEqual([os.path.basename(p) for p in deliver.caption_files(stem + ".mp4")],
                         sorted(os.path.basename(stem + t) for t in (".en.srt", ".VTT", ".srt")))
        self.assertEqual(deliver.caption_files(os.path.join(self.dir, "nope", "x.mp4")), [])

    def test_captions_need_a_subtitle_track_with_something_on_it(self):
        self.assertIn("no subtitle track", deliver.timeline_problems(self.yt, [])[0])
        self.assertIn("empty", deliver.timeline_problems(dest("linkedin_9x16"), [0, 0])[0])
        self.assertEqual(deliver.timeline_problems(self.yt, [0, 3]), [])
        self.assertEqual(deliver.timeline_problems(dest("client_master"), []), [])

    def test_captions_only_on_a_disabled_track_are_refused(self):
        # A disabled track does not render, so its items cannot carry the captions.
        self.assertIn("disabled", deliver.timeline_problems(self.yt, [0], [4])[0])
        self.assertIn("disabled", deliver.timeline_problems(dest("linkedin_9x16"), [], [2])[0])
        self.assertEqual(deliver.timeline_problems(self.yt, [3], [4]), [])
        self.assertEqual(deliver.timeline_problems(dest("client_master"), [], [4]), [])


class TestShape(unittest.TestCase):
    def test_shape_problems(self):
        tall, wide, square = dest("linkedin_9x16"), dest("youtube_16x9"), dest("linkedin_1x1")
        self.assertEqual(deliver.shape_problems(tall, (1080, 1920)), [])
        self.assertEqual(deliver.shape_problems(tall, (2160, 3840)), [])
        self.assertEqual(deliver.shape_problems(wide, (1920, 1080)), [])
        self.assertEqual(deliver.shape_problems(wide, (1920, 1088)), [])  # 0.7% off
        self.assertEqual(deliver.shape_problems(square, (1080, 1080)), [])
        self.assertEqual(deliver.shape_problems(square, (1082, 1080)), [])  # square enough
        for d, size, word in ((tall, (3840, 2160), "landscape"), (wide, (1080, 1920), "portrait"),
                              (square, (1920, 1080), "landscape"),
                              (wide, (4096, 2160), "1.896:1"), (wide, (1920, 800), "2.400:1")):
            with self.subTest(dest=d["key"], size=size):
                problems = deliver.shape_problems(d, size, "timeline 'T'")
                self.assertEqual(len(problems), 1)
                self.assertIn(word, problems[0])
                self.assertIn("timeline 'T'", problems[0])
        self.assertIn("could not be read", deliver.shape_problems(wide, (None, 2160))[0])
        self.assertEqual(deliver.shape_problems(dest("client_master"), (4096, 1716)), [])

    def test_a_size_that_contradicts_the_aspect_is_a_config_problem(self):
        cfg = rpconfig.load_config("/nonexistent/config.json")
        cfg["destinations"]["linkedin_9x16"]["resolution"] = {"width": 1920, "height": 1080}
        with self.assertRaisesRegex(deliver.DeliverError, "9x16"):
            deliver.destination(cfg, "linkedin_9x16")


class TestRenderSteps(unittest.TestCase):
    def settings(self, steps):
        out = {}
        for s in steps:
            out.update(s["settings"])
        return out

    def test_web_destination_sets_every_field(self):
        steps = deliver.render_steps(dest("youtube_16x9"), "/tmp", "SW001_G_01_s_16x9.mp4",
                                     fps=23.976)
        s = self.settings(steps)
        self.assertEqual(steps[0]["settings"]["CustomName"], "SW001_G_01_s_16x9")
        self.assertTrue(steps[0]["required"])
        self.assertEqual((s["FormatWidth"], s["FormatHeight"], s["FrameRate"]),
                         (3840, 2160, 23.976))
        self.assertEqual((s["AudioCodec"], s["AudioSampleRate"]), ("aac", 48000))
        self.assertNotIn("AudioBitDepth", s)
        self.assertEqual((s["ColorSpaceTag"], s["GammaTag"]), ("Rec.709", "Gamma 2.4"))
        self.assertEqual((s["ExportSubtitle"], s["SubtitleFormat"]), (True, "SeparateFile"))
        self.assertIs(s["NetworkOptimization"], True)
        self.assertIs(s["ReplaceExistingFilesInPlace"], False)
        self.assertIs(s["SelectAllFrames"], True)
        optional = {k for st in steps if not st["required"] for k in st["settings"]}
        self.assertEqual(optional, {"FrameRate", "NetworkOptimization",
                                    "ReplaceExistingFilesInPlace"})
        # a data burn-in (timecode on review copies) must not ride along
        self.assertIn({"settings": {"DataBurnIn": "None"}, "required": True}, steps)

    def test_what_the_deliver_page_still_decides_is_named(self):
        steps = deliver.render_steps(dest("youtube_16x9"), "/tmp", "SW001_G_01_s_16x9.mp4",
                                     fps=23.976)
        left = deliver.carried_over(steps)
        for key in ("VideoQuality", "EncodingProfile", "ExportAlpha", "UniqueFilenameStyle",
                    "UseFullExtents", "AddFrameHandles", "PixelAspectRatio"):
            self.assertIn(key, left)
        for key in ("DataBurnIn", "FrameRate", "MarkIn", "MarkOut", "SubtitleFormat"):
            self.assertNotIn(key, left)
        self.assertEqual(set(left) & set(deliver.changed_fields(steps)), set())
        # no frame rate, no data burn-in setting, no captions: each is then left as it is
        left = deliver.carried_over(deliver.render_steps(
            dest("client_master", data_burn_in=None), "/tmp", "C_s_master.mov", size=(8, 8)))
        self.assertIn("FrameRate", left)
        self.assertIn("DataBurnIn", left)
        self.assertNotIn("SubtitleFormat", left)  # captions are off, so it has no effect

    def test_the_data_burn_in_setting_is_configurable(self):
        s = self.settings(deliver.render_steps(dest("linkedin_1x1", data_burn_in="Review TC"),
                                               "/tmp", "x.mp4"))
        self.assertEqual(s["DataBurnIn"], "Review TC")
        s = self.settings(deliver.render_steps(dest("linkedin_1x1", data_burn_in=None),
                                               "/tmp", "x.mp4"))
        self.assertNotIn("DataBurnIn", s)
        cfg = rpconfig.load_config("/nonexistent/config.json")
        cfg["destinations"]["linkedin_1x1"]["data_burn_in"] = ""
        with self.assertRaisesRegex(deliver.DeliverError, "data_burn_in"):
            deliver.destination(cfg, "linkedin_1x1")

    def test_burn_in_none_and_timeline_size(self):
        s = self.settings(deliver.render_steps(dest("linkedin_1x1"), "/tmp", "x.mp4"))
        self.assertEqual((s["ExportSubtitle"], s["SubtitleFormat"]), (True, "BurnIn"))
        self.assertNotIn("FrameRate", s)
        m = dest("client_master")
        s = self.settings(deliver.render_steps(m, "/tmp", "C_s_master.mov", size=(3840, 1600),
                                               fps=25))
        self.assertEqual((s["FormatWidth"], s["FormatHeight"]), (3840, 1600))
        self.assertEqual((s["AudioCodec"], s["AudioBitDepth"]), ("lpcm", 24))
        self.assertEqual(s["ExportSubtitle"], False)
        self.assertNotIn("SubtitleFormat", s)
        with self.assertRaisesRegex(deliver.DeliverError, "timeline's resolution"):
            deliver.render_steps(m, "/tmp", "C_s_master.mov", size=(None, None))

    def test_fps_number(self):
        self.assertEqual(deliver.fps_number("23.976"), 23.976)
        self.assertEqual(deliver.fps_number("29.97 DF"), 29.97)
        self.assertEqual(deliver.fps_number(25), 25.0)
        self.assertIsNone(deliver.fps_number(None))


if __name__ == "__main__":
    unittest.main()
