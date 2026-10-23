"""
list-projects: list every project in the current database folder. Reads Resolve
(Resolve Studio must be running) and changes nothing.
"""

from rpresolve import cli

NAME = "list-projects"
HELP = "List projects in current database"


def add_arguments(p):
    """No flags."""


def run(args):
    resolve = cli.get_resolve()
    pm = resolve.GetProjectManager()
    projects = pm.GetProjectListInCurrentFolder()

    if projects:
        print(f"Projects ({len(projects)}):")
        for p in projects:
            print(f"  - {p}")
    else:
        print("No projects found.")


if __name__ == "__main__":
    cli.script(__name__)
