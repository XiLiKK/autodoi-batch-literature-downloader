"""模块2：元数据解析 — Crossref + Unpaywall"""

import logging
import time
from typing import Optional

import httpx

from .models import PaperMetadata
from .filename import generate_journal_abbr, generate_filename

logger = logging.getLogger(__name__)


class MetadataResolver:
    """通过 Crossref 和 Unpaywall API 获取论文元数据"""

    CROSSREF_BASE = "https://api.crossref.org/works"
    UNPAYWALL_BASE = "https://api.unpaywall.org/v2"

    def __init__(self, email: str = "", timeout: int = 30, max_retries: int = 3):
        self.email = email
        self.timeout = timeout
        self.max_retries = max_retries
        ua = f"PaperDownloader/0.1 (mailto:{email})" if email else "PaperDownloader/0.1"
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": ua},
            follow_redirects=True,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def resolve(self, doi: str, max_title_length: int = 80) -> PaperMetadata:
        """
        解析 DOI 的完整元数据。
        
        流程:
        1. Crossref API 获取基础元数据
        2. Unpaywall API 查找 OA PDF 直链
        3. 解析 DOI 重定向获取真实 landing URL
        4. 生成目标文件名
        """
        meta = PaperMetadata(doi=doi)

        # Step 1: Crossref
        self._fetch_crossref(doi, meta)

        # Step 2: Unpaywall
        self._fetch_unpaywall(doi, meta)

        # Step 2.5: 确保 landing_url 存在
        if not meta.landing_url:
            meta.landing_url = f"https://doi.org/{doi}"

        # Step 2.6: 解析 DOI 重定向，拿到真实 URL（避免浏览器 ERR_ABORTED）
        if meta.landing_url.startswith("https://doi.org/"):
            resolved = self._resolve_doi_redirect(meta.landing_url)
            if resolved:
                meta.landing_url = resolved

        # Step 3: 生成文件名
        meta.target_filename = generate_filename(
            doi=doi,
            title=meta.title,
            year=meta.year,
            journal_abbr=meta.journal_abbr,
            first_author=meta.first_author,
            max_title_length=max_title_length,
        )

        return meta

    def _resolve_doi_redirect(self, doi_url: str) -> Optional[str]:
        """解析 DOI 重定向获取真实落地 URL"""
        try:
            # 不 follow redirect，只拿 Location header
            resp = self._client.get(doi_url)
            # httpx 已经 follow_redirects=True，所以最终 URL 就是真实 URL
            final_url = str(resp.url)
            if final_url != doi_url:
                logger.debug(f"DOI 重定向: {doi_url} -> {final_url}")
                return final_url
        except Exception as e:
            logger.debug(f"DOI 重定向解析失败: {e}")
        return None

    def _fetch_crossref(self, doi: str, meta: PaperMetadata) -> None:
        """从 Crossref 获取元数据"""
        url = f"{self.CROSSREF_BASE}/{doi}"
        params = {}
        if self.email:
            params["mailto"] = self.email

        data = self._get_with_retry(url, params)
        if data is None:
            logger.warning(f"Crossref 无结果: {doi}")
            return

        msg = data.get("message", {})

        # 标题
        titles = msg.get("title", [])
        if titles:
            meta.title = titles[0]

        # 年份: 优先 published-print -> published-online -> issued -> created
        for date_field in ["published-print", "published-online", "published", "issued", "created"]:
            date_obj = msg.get(date_field, {})
            date_parts = date_obj.get("date-parts", [[]])
            if date_parts and date_parts[0] and date_parts[0][0]:
                meta.year = str(date_parts[0][0])
                break

        # 期刊
        containers = msg.get("container-title", [])
        if containers:
            meta.journal = containers[0]

        # 期刊缩写 (强制使用首字母缩写算法，如 JMPS)
        if meta.journal:
            meta.journal_abbr = generate_journal_abbr(meta.journal)

        # 第一作者
        authors = msg.get("author", [])
        if authors:
            first = authors[0]
            meta.first_author = first.get("family", first.get("name", "UNKAUTHOR"))

        # 出版商
        meta.publisher = msg.get("publisher", "")

        # 落地页 URL
        links = msg.get("link", [])
        for link in links:
            if link.get("content-type") == "application/pdf":
                # 有些 Crossref 记录直接包含 PDF 链接
                if not meta.oa_pdf_url:
                    meta.oa_pdf_url = link.get("URL")
            elif link.get("content-type") == "unspecified":
                if not meta.landing_url:
                    meta.landing_url = link.get("URL")

        # 如果没有 landing_url，用 DOI 构造
        if not meta.landing_url:
            meta.landing_url = f"https://doi.org/{doi}"

    def _fetch_unpaywall(self, doi: str, meta: PaperMetadata) -> None:
        """从 Unpaywall 查找 OA PDF 直链"""
        if not self.email:
            logger.debug("未配置邮箱，跳过 Unpaywall")
            return
        url = f"{self.UNPAYWALL_BASE}/{doi}"
        params = {"email": self.email}

        data = self._get_with_retry(url, params)
        if data is None:
            logger.debug(f"Unpaywall 无结果: {doi}")
            return

        if not data.get("is_oa"):
            logger.debug(f"Unpaywall: {doi} 不是 OA")
            return

        best_loc = data.get("best_oa_location", {})
        if best_loc:
            pdf_url = best_loc.get("url_for_pdf")
            if pdf_url:
                meta.oa_pdf_url = pdf_url
                logger.info(f"Unpaywall 找到 OA PDF: {doi}")

            # 也尝试其他 OA location
            if not meta.oa_pdf_url:
                for loc in data.get("oa_locations", []):
                    pdf_url = loc.get("url_for_pdf")
                    if pdf_url:
                        meta.oa_pdf_url = pdf_url
                        break

    def _get_with_retry(self, url: str, params: dict) -> Optional[dict]:
        """带指数退避重试的 GET 请求"""
        for attempt in range(self.max_retries):
            try:
                resp = self._client.get(url, params=params)

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 404:
                    logger.debug(f"404: {url}")
                    return None
                elif resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"429 限流, 等待 {wait}s: {url}")
                    time.sleep(wait)
                    continue
                elif resp.status_code >= 500:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"{resp.status_code} 服务器错误, 等待 {wait}s: {url}")
                    time.sleep(wait)
                    continue
                else:
                    logger.warning(f"HTTP {resp.status_code}: {url}")
                    return None

            except httpx.TimeoutException:
                wait = 2 ** (attempt + 1)
                logger.warning(f"超时, 等待 {wait}s 后重试: {url}")
                time.sleep(wait)
            except httpx.HTTPError as e:
                logger.error(f"HTTP 错误: {e}")
                return None

        logger.error(f"重试耗尽: {url}")
        return None
