"""Package tracked Skills and deployment files without runtime credentials."""

import argparse
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Git commit SHA used as the image tag")
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    args.output.mkdir(parents=True, exist_ok=True)

    skills_root = root / "apps/agent/skills"
    tracked = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--", "apps/agent/skills"], cwd=root
        )
        .decode()
        .split("\0")
    )
    skill_files = [root / name for name in tracked if name]
    if not any(path.name == "SKILL.md" for path in skill_files):
        raise SystemExit("No tracked SKILL.md files to package.")
    with tarfile.open(
        args.output / f"work-assistant-skills-{args.version}.tar.gz", "w:gz"
    ) as archive:
        for path in skill_files:
            archive.add(path, arcname=path.relative_to(skills_root), recursive=False)

    with zipfile.ZipFile(
        args.output / f"work-assistant-deploy-{args.version}.zip",
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.write(root / "deploy/compose.yaml", "compose.yaml")
        archive.write(root / "deploy/README.md", "README.md")
        archive.write(root / "LICENSE", "LICENSE")
        for name in (
            "apps/agent/.env.example",
            "apps/redteam-bridge/.env.example",
            "apps/web/public/config/scenarios.json",
        ):
            archive.write(root / name, name)
        archive.writestr(
            ".env.example",
            f"IMAGE_TAG={args.version}\n"
            "WEB_PORT=5173\n"
            "REDTEAM_PORT=8080\n"
            "SKILLS_ARTIFACT_PATH=./skills\n"
            f"SKILLS_ARTIFACT_VERSION={args.version}\n",
        )
        archive.writestr(
            "version.json",
            json.dumps(
                {
                    "commit": args.version,
                    "skills_version": args.version,
                    "platform": "linux/amd64",
                    "images": [
                        f"vitos-work-assistant-agent:{args.version}",
                        f"vitos-work-assistant-web:{args.version}",
                        f"vitos-redteam-bridge:{args.version}",
                    ],
                },
                indent=2,
            )
            + "\n",
        )


if __name__ == "__main__":
    main()
