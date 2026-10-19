"""Build/check the full account release manifest, without secrets or state."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_manifest(root=ROOT):
    files = {str(path.relative_to(root)) for path in (root / "server").glob("*.py")}
    for folder in ("registration", "mailer"):
        files.update(str(path.relative_to(root)) for path in (root / "server" / folder).rglob("*.py"))
    files.update(str(path.relative_to(root)) for path in (root / "server").glob("requirements*.txt"))
    files.update({
        "server/ARCHITECTURE.md", "server/CHECKIN.md", "server/README.md",
        "server/service-release.json", "server/checkin-release.json",
        "server/deploy-service-upgrade.sh", "server/deploy-checkin-governance.sh",
        "server/deploy-checkin-upgrade.sh", "server/deploy-access-upgrade.sh",
        "OVERVIEW.md", "README.md", "THIRD-PARTY.md",
    })
    assets_root = root / "campus-register"
    assets = {path.name for path in assets_root.iterdir()
              if path.is_file() and path.suffix in {".html", ".css", ".js", ".png", ".ico"}}
    for folder in ("account", "checkin", "shared"):
        assets.update(str(path.relative_to(assets_root)) for path in (assets_root / folder).glob("*.js"))
    return {"files": sorted(files), "assets": sorted(assets),
            "units": ["betasdc-checkin-cleanup.service", "betasdc-checkin-cleanup.timer",
                      "betasdc-mail.service"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate both manifest names")
    arguments = parser.parse_args()
    expected = build_manifest()
    for name in ("service-release.json", "checkin-release.json"):
        path = ROOT / "server" / name
        if arguments.write:
            path.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        elif json.loads(path.read_text()) != expected:
            raise SystemExit(f"{name} is stale; run python -B server/release_manifest.py --write")
    print("Account release manifests are complete")


if __name__ == "__main__":
    main()
