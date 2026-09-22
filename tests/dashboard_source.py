"""Read the authored dashboard for existing source-level regression checks.

Component templates are expanded at their actual mount sites, preserving conditional ancestry.
Runtime navigation and event bindings are covered separately by the browser suite.
"""
from pathlib import Path
import re

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"


def template_source(path: Path) -> str:
    source = path.read_text()
    template = source.split("<template>", 1)[1].rsplit("</template>", 1)[0]
    imports = re.findall(r"import (\w+) from ['\"](.+?\.vue)['\"]", source)
    for name, relative in imports:
        child = template_source((path.parent / relative).resolve())
        template = re.sub(
            rf"<{name}\b([^>]*?)/>",
            lambda match: "<template" + (" " + match[1].strip() if match[1].strip() else "") + ">" + child + "</template>",
            template,
        )
    return template


def dashboard_source() -> str:
    state = FRONTEND / "state"
    computed = "\n".join(p.read_text() for p in sorted(state.glob("*Computed.js")))
    methods = "\n".join(p.read_text() for p in sorted(state.glob("*.js")) if not p.stem.endswith("Computed"))
    return (
        (FRONTEND / "styles/base.css").read_text()
        + template_source(FRONTEND / "App.vue")
        + "\ncomputed:{\n" + computed + "\nmethods:{\n" + methods
    )
