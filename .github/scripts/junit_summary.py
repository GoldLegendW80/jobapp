"""Markdown summary of a JUnit XML report, for $GITHUB_STEP_SUMMARY.

    python3 junit_summary.py <label> <junit.xml>

Prints the totals and up to 30 failing tests with the first line of their message. A missing
report (the job failed before the tests ran) prints a line saying so, and exits 0 either way.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(label: str, path: Path) -> None:
    if not path.is_file():
        print(f"### {label}: no test report (the job stopped before the tests)")
        return
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failed = [
        c for c in cases if c.find("failure") is not None or c.find("error") is not None
    ]
    skipped = [c for c in cases if c.find("skipped") is not None]
    time = sum(float(c.get("time") or 0) for c in cases)
    passed = len(cases) - len(failed) - len(skipped)
    mark = "failed" if failed else "passed"
    print(f"### {label}: {mark}")
    print()
    print("| Tests | Passed | Failed | Skipped | Time |")
    print("|---:|---:|---:|---:|---:|")
    print(f"| {len(cases)} | {passed} | {len(failed)} | {len(skipped)} | {time:.0f}s |")
    if failed:
        print()
        for case in failed[:30]:
            node = case.find("failure")
            if node is None:
                node = case.find("error")
            message = (
                (node.get("message") if node is not None else "") or ""
            ).splitlines()
            first = message[0][:200] if message else ""
            print(f"- `{case.get('classname', '')}::{case.get('name', '')}`: {first}")
    print()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: junit_summary.py <label> <junit.xml>")
    main(sys.argv[1], Path(sys.argv[2]))
