"""Isolated JavaScript rendering and bounded Mongolian/English PDF OCR."""
import asyncio
import ipaddress
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from urllib.parse import urlsplit
from .connectors import ConnectorError

_RENDER_LIMIT = asyncio.Semaphore(1)


def same_public_source(url, host):
    try:
        parsed = urlsplit(url)
        candidate = (parsed.hostname or '').lower().rstrip('.')
        root = host.lower().removeprefix('www.')
        return parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username and not parsed.password and (candidate == root or candidate.endswith('.'+root))
    except ValueError:
        return False


async def public_dns(host):
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


async def render_public_page(url, host, allowed, interaction=None):
    if not same_public_source(url, host) or not allowed(url):
        raise ConnectorError('Browser дүрслэл эх сурвалжийн зөвшөөрсөн хүрээнээс гадуур.')
    if os.getenv('ENABLE_BROWSER_EXTRACTION', 'false').lower() != 'true':
        raise ConnectorError('Browser дүрслэл идэвхжээгүй.')
    try:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    except ImportError as exc:
        raise ConnectorError('Browser runtime суулгагдаагүй.') from exc
    async with _RENDER_LIMIT:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
            try:
                context = await browser.new_context(user_agent='RAINYTransparencyBot/1.0', service_workers='block', accept_downloads=False)
                verified = {}
                async def route_request(route):
                    request = route.request
                    target = request.url
                    if not same_public_source(target, host) or request.resource_type in ('image', 'media', 'font'):
                        await route.abort(); return
                    hostname = urlsplit(target).hostname
                    if hostname not in verified:
                        verified[hostname] = await public_dns(hostname)
                    if not verified[hostname] or not allowed(target):
                        await route.abort(); return
                    await route.continue_()
                await context.route('**/*', route_request)
                page = await context.new_page()
                async with asyncio.timeout(120 if interaction else 40):
                    response = await page.goto(url, wait_until='domcontentloaded', timeout=25000)
                    if response and response.status >= 400:
                        raise ConnectorError('Browser HTTP '+str(response.status))
                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except PlaywrightTimeout:
                        pass
                    # Wait for actual visible content, rather than saving an empty app shell.
                    await page.wait_for_function('document.body && document.body.innerText.trim().length > 150', timeout=10000)
                    text = (await page.locator('body').inner_text()).casefold()
                    if any(marker in text for marker in ('verify you are human', 'checking your browser', 'captcha', 'нэвтрэх шаардлагатай')):
                        raise ConnectorError('Хандалтын шалгалт эсвэл нэвтрэлт шаардлагатай; автоматаар тойрохгүй.')
                    if interaction is not None:
                        return await interaction(page, context)
                    html = (await page.content()).encode('utf-8')
                    if len(html) > 5_000_000:
                        raise ConnectorError('Дүрслэгдсэн HTML 5 MB хязгаараас их.')
                    return html, page.url
            finally:
                await browser.close()


def ocr_pdf(raw, max_pages=10):
    if os.getenv('ENABLE_PDF_OCR', 'false').lower() != 'true':
        raise ConnectorError('PDF OCR идэвхжээгүй.')
    rows = []
    with tempfile.TemporaryDirectory(prefix='rainy-ocr-') as directory:
        pdf = Path(directory)/'document.pdf'
        pdf.write_bytes(raw)
        try:
            subprocess.run(['pdftoppm', '-f', '1', '-l', str(min(max_pages, 10)), '-scale-to', '1800', '-png', str(pdf), str(Path(directory)/'page')], check=True, capture_output=True, timeout=45)
            for image in sorted(Path(directory).glob('page-*.png')):
                result = subprocess.run(['tesseract', str(image), 'stdout', '-l', 'mon+eng'], check=True, capture_output=True, text=True, timeout=30)
                text = result.stdout.strip()
                if text:
                    rows.append({'page_number': int(image.stem.split('-')[-1]), 'text': text[:20000], 'extraction': 'ocr', 'verification': 'Танилтын алдаа байж болно; эх PDF-тэй тулгана.'})
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            raise ConnectorError('OCR runtime эсвэл PDF боловсруулах ажиллагаа амжилтгүй.') from exc
    return rows
