"""The redesigned dashboard must receive real local assets, not an HTML fallback."""

from pathlib import Path
import re

from httpx import AsyncClient

from treg import api


async def test_dashboard_redesign_assets_are_served_from_the_same_origin(clients: AsyncClient):
    page = await clients.get('/app')
    assert page.status_code == 200
    paths = set(re.findall(r'(?:href|src)="(/media/redesign/[^"\']+)"', page.text))
    assets = set(re.findall(r'(?:href|src)="(/app/ui/assets/[^"\']+)"', page.text))
    assert any(p.endswith('.js') for p in assets)
    assert any(p.endswith('.css') for p in assets)
    assert page.headers['cache-control'] == 'no-cache'
    for asset in assets:
        response = await clients.get(asset)
        assert response.status_code == 200
        assert 'immutable' in response.headers['cache-control']
        assert not response.headers['content-type'].startswith('text/html')
    missing = await clients.get('/app/ui/assets/missing.js')
    assert missing.status_code == 404
    assert '<!doctype' not in missing.text.lower()
    # Include the dynamic task, provider and token-state images as well as literal URLs.
    asset_dir = Path(api.__file__).parent / 'web' / 'media' / 'redesign'
    paths.update('/media/redesign/' + p.name for p in asset_dir.iterdir() if p.suffix in {'.svg', '.png', '.jpg'})
    for path in sorted(paths):
        response = await clients.get(path)
        assert response.status_code == 200, path
        assert response.content, path
        expected_type = 'text/css' if path.endswith('.css') else 'image/'
        assert response.headers['content-type'].startswith(expected_type), path


async def test_dashboard_dev_entry_preserves_same_origin_requests(clients, monkeypatch):
    from treg.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, 'frontend_dev', True)
    monkeypatch.setattr(settings, 'public_url', 'http://localhost:18790')
    response = await clients.get('/app')
    assert response.status_code == 200
    assert 'http://localhost:5173/app/ui/@vite/client' in response.text
    assert 'http://localhost:5173/app/ui/src/main.ts' in response.text
    assert '/agent-setup.js' in response.text
    assert response.headers['cache-control'] == 'no-cache'


async def test_dashboard_dev_entry_refuses_a_public_hostname(clients, monkeypatch):
    import pytest
    from treg.config import get_settings
    monkeypatch.setattr(get_settings(), 'frontend_dev', True)
    monkeypatch.setattr(get_settings(), 'public_url', 'https://registry.example.com')
    with pytest.raises(RuntimeError, match='loopback'):
        await clients.get('/app')
