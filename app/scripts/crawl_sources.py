"""Discover likely opportunity links from the curated source catalog.

The crawler emits candidates for human review; it does not create or publish opportunities.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data" / "seed_sources.json"
DEFAULT_OUTPUT = ROOT / "data" / "crawl_candidates.json"
MAX_RESPONSE_BYTES = 2_000_000
MAX_LINKS_PER_PAGE = 2_000
USER_AGENT = "CompassOpportunityDiscovery/1.0"

OPPORTUNITY_TERMS = (
    "opportun", "scholar", "fellow", "intern", "job", "career", "vacanc", "position",
    "opening", "grant", "admission", "graduate", "funding", "recruit",
)
SECTION_TERMS = (
    "opportun", "scholar", "fellow", "intern", "job", "career", "vacanc", "position",
    "opening", "grant", "admission", "graduate", "funding",
)
CATEGORY_HINTS = {
    "admissions": "admission",
    "scholarships": "scholarship",
    "funding": "scholarship",
    "internships": "internship",
    "jobs": "job",
    "remote": "job",
    "techjobs": "job",
}


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            if self._href:
                self.anchors.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None
            self._text = []


class PageFetchError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _http_url(value: str, base: str | None = None) -> str | None:
    absolute = urldefrag(urljoin(base, value) if base else value)[0]
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    return absolute


def _source_category(key: str) -> str | None:
    return CATEGORY_HINTS.get(key.split("-", 1)[0].lower())


def _fetch_html(client: httpx.Client, url: str) -> str:
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise PageFetchError(f"http_{response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            return ""
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise PageFetchError("page_too_large")
        return body.decode(response.encoding or "utf-8", errors="replace")


def _has_term(value: str, terms: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in terms)


def crawl_sources(
    sources: list[dict],
    *,
    client: httpx.Client,
    max_sources: int | None = None,
    max_pages_per_source: int = 4,
    delay_seconds: float = 0.5,
    sleep=time.sleep,
) -> dict:
    selected = sources[:max_sources] if max_sources is not None else sources
    candidates: dict[tuple[str, str], dict] = {}
    source_results = []
    failures = []
    last_request: dict[str, float] = {}

    for source in selected:
        key = str(source.get("key", ""))
        source_url = _http_url(str(source.get("url", "")))
        if not key or not source_url or urlsplit(source_url).scheme != "https":
            source_results.append({"key": key, "status": "failed", "pages_crawled": 0})
            failures.append({"key": key, "code": "invalid_source"})
            continue

        source_host = urlsplit(source_url).hostname
        queue = [source_url]
        visited: set[str] = set()
        source_errors: list[str] = []
        pages_crawled = 0

        while queue and pages_crawled < max_pages_per_source:
            page_url = queue.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            now = time.monotonic()
            wait = delay_seconds - (now - last_request.get(source_host, 0.0))
            if wait > 0:
                sleep(wait)
            last_request[source_host] = time.monotonic()
            try:
                html = _fetch_html(client, page_url)
            except PageFetchError as exc:
                source_errors.append(exc.code)
                failures.append({"key": key, "url": page_url, "code": exc.code})
                continue
            except httpx.TimeoutException:
                source_errors.append("timeout")
                failures.append({"key": key, "url": page_url, "code": "timeout"})
                continue
            except httpx.HTTPError:
                source_errors.append("network_error")
                failures.append({"key": key, "url": page_url, "code": "network_error"})
                continue

            pages_crawled += 1
            parser = AnchorParser()
            parser.feed(html)
            for href, title in parser.anchors[:MAX_LINKS_PER_PAGE]:
                target = _http_url(href, page_url)
                if target is None:
                    continue
                target_parts = urlsplit(target)
                label = f"{title} {target_parts.path}"
                if _has_term(label, OPPORTUNITY_TERMS):
                    path_title = target_parts.path.rstrip("/").split("/")[-1].replace("-", " ")
                    candidates[(key, target)] = {
                        "source_key": key,
                        "source_name": source.get("name", key),
                        "source_url": source_url,
                        "url": target,
                        "title": title or path_title or target,
                        "category_hint": _source_category(key),
                    }
                if (
                    target_parts.hostname == source_host
                    and target not in visited
                    and _has_term(label, SECTION_TERMS)
                    and len(queue) + pages_crawled < max_pages_per_source
                ):
                    queue.append(target)

        if source_errors and pages_crawled:
            status = "partial"
        elif source_errors:
            status = "failed"
        else:
            status = "ok"
        source_results.append({
            "key": key,
            "status": status,
            "pages_crawled": pages_crawled,
            "errors": source_errors,
        })

    return {
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "sources": source_results,
        "candidates": list(candidates.values()),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover opportunity links from the source catalog")
    parser.add_argument("--limit", type=int, help="maximum number of sources to fetch")
    parser.add_argument("--max-pages", type=int, default=4, help="maximum pages per source (1-10)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="candidate report JSON path")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if not 1 <= args.max_pages <= 10:
        parser.error("--max-pages must be between 1 and 10")

    catalog = json.loads(CATALOG.read_text(encoding="utf-8-sig"))
    with httpx.Client(
        timeout=httpx.Timeout(15, connect=5),
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    ) as client:
        report = crawl_sources(
            catalog.get("sources", []),
            client=client,
            max_sources=args.limit,
            max_pages_per_source=args.max_pages,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pages = sum(source["pages_crawled"] for source in report["sources"])
    failed = sum(source["status"] == "failed" for source in report["sources"])
    print(
        f"Crawl complete: sources={len(report['sources'])}, pages={pages}, "
        f"candidates={len(report['candidates'])}, failed_sources={failed}, output={args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())