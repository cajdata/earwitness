"""Earwitness: geolocate recordings from background thunder and prop-aircraft sound."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("earwitness")
except PackageNotFoundError:  # pragma: no cover - source tree without an installed dist
    __version__ = "0.0.0"

__all__ = ["__version__"]
