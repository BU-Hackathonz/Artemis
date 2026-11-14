"""Manual check: python -m artemis.echo PAGE [AFTER_PAGE]. Prints the outline and the spoken summary."""

import asyncio
import sys

from .check import EchoError, compare, echo_check, speak_report


async def main(paths: list[str]) -> int:
    reports = []
    for path in paths:
        try:
            report = await echo_check(path)
        except EchoError as exc:
            print(f"Echo Check: {exc}")
            return 1
        reports.append(report)
        print(f"\n== {report.title} ({report.url})")
        print("\n".join(report.outline))
        for f in report.findings:
            print(f"  [{f.impact}] {f.rule} x{f.count} at {f.target}: {f.message}")
        print("Spoken:", speak_report(report))
    if len(reports) == 2:
        print("\nSpoken:", compare(*reports))
    return 0


if __name__ == "__main__":
    if not sys.argv[1:]:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1:3])))
