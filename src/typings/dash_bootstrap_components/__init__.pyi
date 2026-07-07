# dash_bootstrap_components ships no py.typed marker (no inline types, no
# stub package on PyPI). All of its components are thin, dynamically
# generated wrappers around arbitrary HTML/Bootstrap props, so a single
# catch-all is the accurate typing: every attribute (component class,
# `themes.*` constant, ...) is Any.
from typing import Any

def __getattr__(name: str) -> Any: ...
