"""
survey: read-only survey of every project on disk: grades, timelines and media
from Resolve's databases, never through the API, so it is safe while Resolve is
open. It runs resolve_survey.py under Python 3.14, which has compression.zstd.
"""

import sys

from rpresolve import cli
from rpresolve import workflows as rpwork

NAME = "survey"
HELP = "Read-only survey of every project on disk (offline, no Resolve API)"

# The survey decodes zstd blobs, which needs compression.zstd (Python 3.14+).
# The script runs on the system python3 (3.9), so survey runs the sibling
# resolve_survey.py as a subprocess under this interpreter instead.
SURVEY_PYTHON = rpwork.SURVEY_PYTHON
SURVEY_SCRIPT = rpwork.SURVEY_SCRIPT


def build_survey_command(args, python=SURVEY_PYTHON, script=SURVEY_SCRIPT):
    """The argv that runs resolve_survey.py for a parsed 'survey' command."""
    return rpwork.survey_command(args.out, args.json, args.projects, args.projects_dir,
                                 args.metadata_cache, args.no_metadata_cache, args.tree_labels,
                                 python=python, script=script)


def add_arguments(p):
    p.add_argument("--out", required=True,
                   help="Markdown report path (refused inside this repository)")
    p.add_argument("--json", help="Also write the survey as JSON here")
    p.add_argument("--projects", nargs="+", metavar="NAME",
                   help="Only these projects (default: every project folder)")
    p.add_argument("--projects-dir", help="Resolve's Projects folder (default: the disk database)")
    p.add_argument("--metadata-cache",
                   help="Resolve's ProjectMetadataCache/Metadata.db to cross-check against "
                        "(default: the live one)")
    p.add_argument("--no-metadata-cache", action="store_true",
                   help="Skip the cross-check against Resolve's metadata cache")
    p.add_argument("--tree-labels", nargs="+", metavar="LABEL",
                   help="Node labels that mark the house node tree (default: the survey's)")


def run(args):
    """Run the read-only project survey. It reads Project.db snapshots from
    disk and never connects to Resolve, so it is safe while Resolve is open."""
    sys.stdout.flush()
    try:
        return rpwork.survey(args.out, args.json, args.projects, args.projects_dir,
                             args.metadata_cache, args.no_metadata_cache,
                             args.tree_labels)["returncode"]
    except rpwork.Refused as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    cli.script(__name__)
