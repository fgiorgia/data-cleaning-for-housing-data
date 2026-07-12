"""Nashville-area bounding box, shared by the geocoder and export checks.

Single source of truth for the accepted coordinate range: the geocoding
service rejects provider results that land outside it (ambiguous street
names like "MADISON" or "OLD HICKORY" resolve to the wrong state
otherwise), and tests/test_export_invariants.py asserts every exported
coordinate falls inside it. Generous margins around Davidson County.
"""

from __future__ import annotations

MIN_LATITUDE: float = 35.0
MAX_LATITUDE: float = 36.7
MIN_LONGITUDE: float = -87.6
MAX_LONGITUDE: float = -85.7


def is_within_nashville_bounds(latitude: float, longitude: float) -> bool:
    """True when the point falls inside the Nashville-area bounding box."""
    return (
        MIN_LATITUDE <= latitude <= MAX_LATITUDE
        and MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
    )
