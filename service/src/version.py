"""Single source of truth for the running service version.

Read from the installed ``duar`` distribution metadata so it tracks
``service/pyproject.toml`` automatically. The release script bumps that file,
and this value follows without any code change — preventing the drift where
hardcoded literals lagged the released version.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("duar-service")
except PackageNotFoundError:  # pragma: no cover - running from a raw checkout
    __version__ = "0.0.0+unknown"
