import httpx

from app.scripts.crawl_sources import crawl_sources


def test_crawler_discovers_candidates_without_fetching_external_links():
    fetched = []

    def respond(request):
        fetched.append(str(request.url))
        if request.url.path == "/":
            html = (
                '<a href="/opportunities">Opportunities</a>'
                '<a href="https://apply.example.net/req/1">Data Analyst Internship</a>'
                '<a href="mailto:help@example.org">Contact</a>'
            )
        else:
            html = '<a href="https://apply.example.net/jobs/2">Junior Engineer role</a>'
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
        report = crawl_sources(
            [{
                "key": "internships-test",
                "name": "Test Internships",
                "url": "https://source.example.org/",
            }],
            client=client,
            max_pages_per_source=2,
            delay_seconds=0,
        )

    assert fetched == ["https://source.example.org/", "https://source.example.org/opportunities"]
    assert report["sources"][0]["status"] == "ok"
    assert report["sources"][0]["pages_crawled"] == 2
    assert {item["url"] for item in report["candidates"]} == {
        "https://source.example.org/opportunities",
        "https://apply.example.net/req/1",
        "https://apply.example.net/jobs/2",
    }
    assert all(item["category_hint"] == "internship" for item in report["candidates"])