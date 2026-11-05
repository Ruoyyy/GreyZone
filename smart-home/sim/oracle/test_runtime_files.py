"""Check the retained runtime inputs without importing Home Assistant."""

import json
from pathlib import Path


SIM = Path(__file__).resolve().parents[1]


def test_platform_image_matches_locked_dependency():
    lock = json.loads((SIM / "upstreams.lock.json").read_text())
    image = lock["home_assistant"]["image"]
    assert "@sha256:" in image
    assert (SIM / "Dockerfile").read_text().splitlines()[0] == f"FROM {image}"
    assert (SIM / lock["home_assistant"]["license"]).is_file()
    assert (SIM / lock["hga"]["source_path"] / "custom_components/home_generative_agent").is_dir()


def test_hga_manifest_requirements_are_available_to_image_build():
    hga = SIM.parent / "hga"
    manifest = json.loads(
        (hga / "custom_components/home_generative_agent/manifest.json").read_text()
    )
    bundled = {
        line.strip()
        for line in (hga / "requirements_runtime_manifest.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert bundled == set(manifest["requirements"])
