"""The launch destination must work for a signed-out viewer, including its bundled assets."""

import re
from urllib.parse import urlsplit

from httpx import AsyncClient


async def test_astra_is_public_indexable_and_answers_head(clients: AsyncClient):
    clients.cookies.clear()
    response = await clients.get("/astra?utm_source=launch-video")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert '<link rel="canonical" href="https://treg.to/astra">' in response.text
    assert 'href="https://chatgpt.com/plugins/plugins_6a7fb961a34881918798681dade464ec?q=treg"' in response.text
    assert 'class="start-section"' not in response.text
    assert 'type="email"' in response.text
    assert 'data-page="astra"' in response.text
    assert 'href="/auth/google"' in response.text
    assert 'href="/auth/github"' in response.text
    assert 'role="tablist"' in response.text
    assert 'src="/sitetrack.js"' in response.text
    assert 'src="/adtrack.js"' in response.text
    head = await clients.head("/astra")
    assert head.status_code == 200
    assert not head.content
    assert "/astra</loc>" in (await clients.get("/sitemap.xml")).text


async def test_astra_bundled_assets_and_seekable_film(clients: AsyncClient):
    page = (await clients.get("/astra")).text
    urls = set(re.findall(r'(?:src|poster|href)="((?:media|logos)/[^"#]+)"', page))
    for url in urls:
        response = await clients.get("/" + urlsplit(url).path)
        assert response.status_code == 200, url
    # The film is loaded on demand and must support a browser seeking within it.
    assert '<video id="launch-film" controls playsinline preload="none"' in page
    assert 'src="media/astra/launch.mp4"' not in page
    video = await clients.get("/media/astra/launch.mp4", headers={"Range": "bytes=0-127"})
    assert video.status_code == 206
    assert len(video.content) == 128
    assert video.headers["content-type"] == "video/mp4"
