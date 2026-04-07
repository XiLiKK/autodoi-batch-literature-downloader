"""下载器包"""

from .base import BaseDownloader
from .oa_direct import OADirectDownloader
from .springer import SpringerDownloader
from .elsevier import ElsevierDownloader
from .wiley import WileyDownloader
from .generic import GenericDownloader
from .scihub import SciHubDownloader

__all__ = [
    "BaseDownloader",
    "OADirectDownloader",
    "SpringerDownloader",
    "ElsevierDownloader",
    "WileyDownloader",
    "GenericDownloader",
    "SciHubDownloader",
]
