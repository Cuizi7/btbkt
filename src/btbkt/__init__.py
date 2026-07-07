__all__ = ["__version__"]

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("btbkt")
except PackageNotFoundError:
    __version__ = "0+unknown"
