"""模块4：下载策略路由"""

import logging
from urllib.parse import urlparse

from .models import PaperMetadata
from .constants import DownloadRoute

logger = logging.getLogger(__name__)

# 出版商名称到路由的映射（部分匹配）
_PUBLISHER_ROUTES: list[tuple[list[str], DownloadRoute]] = [
    (["springer", "nature", "biomed central", "bmc"], DownloadRoute.SPRINGER),
    (["elsevier", "sciencedirect", "cell press"], DownloadRoute.ELSEVIER),
    (["wiley", "john wiley"], DownloadRoute.WILEY),
]

# 域名到路由的映射
_DOMAIN_ROUTES: dict[str, DownloadRoute] = {
    "link.springer.com": DownloadRoute.SPRINGER,
    "nature.com": DownloadRoute.SPRINGER,
    "www.nature.com": DownloadRoute.SPRINGER,
    "bmcbioinformatics.biomedcentral.com": DownloadRoute.SPRINGER,
    "www.sciencedirect.com": DownloadRoute.ELSEVIER,
    "sciencedirect.com": DownloadRoute.ELSEVIER,
    "www.cell.com": DownloadRoute.ELSEVIER,
    "linkinghub.elsevier.com": DownloadRoute.ELSEVIER,
    "onlinelibrary.wiley.com": DownloadRoute.WILEY,
    "www.wiley.com": DownloadRoute.WILEY,
}


def route_download(meta: PaperMetadata) -> DownloadRoute:
    """
    根据元数据决定下载策略。
    
    优先级:
    1. 有 OA PDF 直链 -> OA_DIRECT
    2. 按出版商匹配
    3. 按域名匹配
    4. 通用策略
    """
    # 1. OA 直链优先
    if meta.oa_pdf_url:
        logger.info(f"路由: OA_DIRECT (有直链) - {meta.doi}")
        return DownloadRoute.OA_DIRECT

    # 2. 按出版商匹配
    publisher_lower = meta.publisher.lower()
    for keywords, route in _PUBLISHER_ROUTES:
        if any(kw in publisher_lower for kw in keywords):
            logger.info(f"路由: {route.value} (出版商: {meta.publisher}) - {meta.doi}")
            return route

    # 3. 按域名匹配
    if meta.landing_url:
        try:
            domain = urlparse(meta.landing_url).hostname
            if domain:
                # 精确匹配
                if domain in _DOMAIN_ROUTES:
                    route = _DOMAIN_ROUTES[domain]
                    logger.info(f"路由: {route.value} (域名: {domain}) - {meta.doi}")
                    return route
                # 子域名匹配
                for known_domain, route in _DOMAIN_ROUTES.items():
                    if domain.endswith("." + known_domain) or domain == known_domain:
                        logger.info(f"路由: {route.value} (域名: {domain}) - {meta.doi}")
                        return route
        except Exception:
            pass

    # 4. 通用策略
    logger.info(f"路由: GENERIC - {meta.doi}")
    return DownloadRoute.GENERIC
