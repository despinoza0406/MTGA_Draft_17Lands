"""
Tools/check_upstream.py
Checks whether the original repository (remote "upstream") has changes this
branch does not, and whether they merge cleanly and keep the tests green.
Nothing in your checkout changes: the trial merge runs in a temporary worktree.

Usage:
    python Tools/check_upstream.py [--report report.txt] [--python <interpreter for pytest>]
"""

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = "upstream/main"


def git(*args, cwd=REPO, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def check(python: str) -> list:
    lines = [f"Upstream check {datetime.datetime.now():%Y-%m-%d %H:%M}"]
    git("fetch", "--quiet", "upstream")
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    new_commits = git("log", "--oneline", "--no-merges", f"HEAD..{UPSTREAM}").stdout.strip().splitlines()
    if not new_commits:
        return lines + [f"{branch} already contains everything in {UPSTREAM}."]

    lines.append(f"{len(new_commits)} upstream commit(s) not in {branch}:")
    lines += [f"  {c}" for c in new_commits]
    base = git("merge-base", "HEAD", UPSTREAM).stdout.strip()
    lines += ["", "Files changed upstream:"]
    lines += ["  " + l for l in git("diff", "--stat", base, UPSTREAM).stdout.strip().splitlines()]

    worktree = tempfile.mkdtemp(prefix="upstream-check-")
    try:
        git("worktree", "add", "--detach", worktree, "HEAD")
        merge = git("merge", "--no-commit", "--no-ff", UPSTREAM, cwd=worktree, check=False)
        conflicts = git("diff", "--name-only", "--diff-filter=U", cwd=worktree).stdout.split()
        if merge.returncode != 0 or conflicts:
            lines += ["", "VERDICT: merge has conflicts, needs manual work in:"]
            lines += [f"  {c}" for c in conflicts] or ["  " + merge.stdout.strip()[-300:]]
            return lines

        tests = subprocess.run(
            [python, "-m", "pytest", "tests", "-q", "-p", "no:warnings"],
            cwd=worktree,
            capture_output=True,
            text=True,
        )
        summary = (tests.stdout.strip().splitlines() or ["(no pytest output)"])[-1]
        if tests.returncode == 0:
            lines += ["", f"VERDICT: merges cleanly and tests pass ({summary})."]
            lines.append(f"To bring it in: git merge {UPSTREAM}")
        else:
            failed = [l for l in tests.stdout.splitlines() if l.startswith("FAILED")]
            lines += ["", f"VERDICT: merges cleanly but tests fail ({summary}):"]
            lines += [f"  {f}" for f in failed[:20]]
        return lines
    finally:
        git("worktree", "remove", "--force", worktree, check=False)
        shutil.rmtree(worktree, ignore_errors=True)
        git("worktree", "prune", check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", help="Also write the report to this file")
    parser.add_argument("--python", default=sys.executable, help="Interpreter used to run the tests")
    args = parser.parse_args()

    report = "\n".join(check(args.python))
    print(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
