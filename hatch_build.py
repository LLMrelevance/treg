"""Require prebuilt dashboard assets in distributable packages, never in editable installs."""
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return
        index = Path(self.root) / "src/treg/web/dashboard/index.html"
        if not index.is_file():
            raise RuntimeError(
                "Dashboard assets are missing. Run bash scripts/build-dashboard.sh before uv build."
            )
        legacy = Path(self.root) / "src/treg/web/dashboard-legacy/index.html"
        if not legacy.is_file():
            raise RuntimeError("Frozen legacy dashboard is missing; both frontends must ship during rollout.")
        build_data["artifacts"].append("src/treg/web/dashboard/**")
