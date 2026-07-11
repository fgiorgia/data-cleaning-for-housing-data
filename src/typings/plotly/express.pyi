# plotly ships no py.typed marker. plotly.express is a large collection of
# figure-factory functions with highly variable signatures; a catch-all
# keeps call sites honest (Any in, Any out) without hand-typing the whole
# module surface.
from typing import Any

def __getattr__(name: str) -> Any: ...
