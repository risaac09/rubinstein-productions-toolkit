"""
list-timelines: list every timeline in the current project with its length,
marking the active one. Reads Resolve (Resolve Studio must be running) and
changes nothing.
"""

from rpresolve import cli

NAME = "list-timelines"
HELP = "List timelines in current project"


def add_arguments(p):
    """No flags."""


def run(args):
    resolve = cli.get_resolve()
    project = cli.get_project(resolve)
    count = project.GetTimelineCount()

    if count == 0:
        print("No timelines in current project.")
        return

    current = project.GetCurrentTimeline()
    current_name = current.GetName() if current else ""
    fps = cli.safe_fps(project.GetSetting("timelineFrameRate"), default=24.0)

    print(f"Timelines ({count}):")
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            marker = " <- active" if tl.GetName() == current_name else ""
            duration_frames = tl.GetEndFrame() - tl.GetStartFrame()
            duration_sec = duration_frames / fps
            mins = int(duration_sec // 60)
            secs = int(duration_sec % 60)
            print(f"  {i}. {tl.GetName()} ({mins}:{secs:02d}){marker}")


if __name__ == "__main__":
    cli.script(__name__)
