# plotly ships no py.typed marker. `Figure` is declared explicitly because
# this repo uses it as a return-type annotation; everything else in the
# module (trace types such as Scattermap/Scattermapbox, etc.) falls back to
# the catch-all since call sites only ever construct-and-pass them through.
from typing import Any

class Figure:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def __getattr__(self, name: str) -> Any: ...

def __getattr__(name: str) -> Any: ...
