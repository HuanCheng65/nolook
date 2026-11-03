"""Set the version everywhere and regenerate CHANGELOG.md.

    uv run scripts/bump.py 0.4.0

Then commit (`chore(release): 0.4.0`), push, and push a signed tag v0.4.0;
the release workflow publishes to PyPI and creates the GitHub release.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print("usage: scripts/bump.py X.Y.Z", file=sys.stderr)
        return 2
    v = sys.argv[1]
    py = ROOT / "pyproject.toml"
    py.write_text(re.sub(r'(?m)^version = "[^"]+"', f'version = "{v}"', py.read_text(), count=1))
    plugin = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
    data = json.loads(plugin.read_text())
    data["version"] = v
    plugin.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    # The tag does not exist yet: name the unreleased commits as this version.
    subprocess.run(["uvx", "git-cliff", "--tag", f"v{v}", "-o", "CHANGELOG.md"], cwd=ROOT, check=True)
    print(f"version {v} in pyproject.toml and plugin.json; CHANGELOG.md regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
