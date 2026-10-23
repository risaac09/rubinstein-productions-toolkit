"""
info: show the current project's resolution, frame rate, colour science and
timeline count, the active timeline's tracks and length, and any pending render
jobs. Reads Resolve (Resolve Studio must be running) and changes nothing. The
MCP tools resolve_status, list_timelines and render_queue_status cover it
together.
"""

from rpresolve import cli

NAME = "info"
HELP = "Show project/timeline info"


def add_arguments(p):
    """No flags."""


def run(args):
    resolve = cli.get_resolve()
    project = cli.get_project(resolve)

    print(f"Project: {project.GetName()}")
    print(f"  Resolution: {project.GetSetting('timelineResolutionWidth')}x{project.GetSetting('timelineResolutionHeight')}")
    print(f"  Frame Rate: {project.GetSetting('timelineFrameRate')}")
    print(f"  Color Science: {project.GetSetting('colorScienceMode')}")
    print(f"  Timelines: {project.GetTimelineCount()}")

    timeline = project.GetCurrentTimeline()
    if timeline:
        print(f"\nActive Timeline: {timeline.GetName()}")
        print(f"  Video Tracks: {timeline.GetTrackCount('video')}")
        print(f"  Audio Tracks: {timeline.GetTrackCount('audio')}")
        print(f"  Subtitle Tracks: {timeline.GetTrackCount('subtitle')}")

        fps = cli.safe_fps(project.GetSetting("timelineFrameRate"), default=24.0)
        start = timeline.GetStartFrame()
        end = timeline.GetEndFrame()
        duration = (end - start) / fps
        print(f"  Duration: {int(duration // 60)}:{int(duration % 60):02d} ({end - start} frames)")
    else:
        print("\nNo active timeline.")

    jobs = project.GetRenderJobList() or []
    if jobs:
        print(f"\nRender queue: {len(jobs)} job(s) pending (remove jobs on the Deliver page).")


if __name__ == "__main__":
    cli.script(__name__)
