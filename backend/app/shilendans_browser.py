"""Read the observed public organization search UI, not its homepage menus."""
import asyncio
import hashlib
import re
from urllib.robotparser import RobotFileParser
import httpx
from .web_discovery import download
from .document_extraction import render_public_page, same_public_source
from .connectors import ConnectorError

URL = 'https://shilendans.gov.mn/organization'


def profile_fields(name, url, details, reports):
    fields = {'organization_name': name, 'company_name': name, 'source_organization_url': url,
              'record_type': 'organization_profile', 'available_report_names': reports,
              'verification': 'Нийтийн порталын мэдэгдэл; хүчинтэй байдал, ижил этгээдийг тусад нь нягтална.'}
    for label, field, pattern in [
        ('Байгууллагын регистрийн дугаар', 'registration', r'[\w\-]+'),
        ('Толгой байгууллага', 'parent_organization_name', r'[^\n]+'),
        ('Байгууллагын төрөл', 'organization_type', r'[^\n]+'),
        ('Төрд ногдох хувь хөрөнгө', 'state_share_percent', r'\d+(?:[.,]\d+)?'),
    ]:
        match = re.search(re.escape(label)+r'\s*:\s*('+pattern+')', details)
        if match:
            fields[field] = match.group(1).strip()[:300]
    return fields


async def search_organizations(query, limit=3):
    # Respect the source's published crawl policy before launching Chromium.
    parser = RobotFileParser()
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        try:
            raw, _, _ = await download(client, 'https://shilendans.gov.mn/robots.txt', 'shilendans.gov.mn')
            parser.parse(raw.decode('utf-8', errors='replace').splitlines())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise ConnectorError('Шилэн дансны robots.txt-г шалгаж чадсангүй.') from exc
            parser.parse([])
    allowed = lambda url: parser.can_fetch('RAINYTransparencyBot', url)
    if not allowed(URL):
        return {'status':'robots_denied','records':[],'pages':[{'url':URL,'status':'robots_denied'}]}
    if (parser.crawl_delay('RAINYTransparencyBot') or 0) > 5:
        raise ConnectorError('Эх сурвалжийн crawl-delay нь тусдаа worker шаарддаг.')

    async def interact(page, context):
        field = page.get_by_placeholder('Кириллээр бичнэ үү!', exact=True)
        await field.wait_for(timeout=15000)
        if re.fullmatch(r'\d{7,12}', query.strip()):
            await page.locator('.ant-select').filter(has_text='Нэрээр').first.click()
            await page.locator('.ant-select-item-option-content').get_by_text('Регистрээр', exact=True).click()
        await field.fill(query)
        # The observed UI debounces input. Wait for filtered cards, not a fixed sleep
        # or the old unfiltered list, and require all query tokens in their names.
        await page.wait_for_function('''q => {
          const busy=document.querySelector('.ant-spin-spinning');
          const names=[...document.querySelectorAll('.ant-card-body strong')].map(e=>e.innerText.toLocaleLowerCase());
          return !busy && (names.some(n=>q.toLocaleLowerCase().split(/\\s+/).every(t=>n.includes(t))) || /Нийт:\\s*0/.test(document.body.innerText));
        }''', arg=query if not query.isdigit() else '', timeout=20000)
        # Give the request initiated by the input a chance to settle, including zero results.
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        names = await page.locator('.ant-card-body strong').all_text_contents()
        if not query.isdigit():
            names = [n.strip() for n in names if all(t in n.casefold() for t in query.casefold().split())]
        names = sorted(dict.fromkeys(names), key=lambda n:(n.casefold()!=query.casefold(),len(n)))
        records=[]; pages=[{'url':page.url,'status':'read','rows_read':len(names),'matched_rows':len(names),'extraction_method':'browser_form'}]
        for name in names[:limit]:
            try:
                await asyncio.sleep(max(.5, parser.crawl_delay('RAINYTransparencyBot') or 0))
                async with page.expect_popup(timeout=12000) as popup:
                    await page.get_by_text(name, exact=True).click()
                detail = await popup.value
                await detail.wait_for_load_state('domcontentloaded', timeout=20000)
                if not same_public_source(detail.url, 'shilendans.gov.mn') or not allowed(detail.url):
                    await detail.close()
                    raise ConnectorError('Байгууллагын холбоос зөвшөөрсөн хүрээнээс гадуур.')
                await detail.get_by_role('button',name='Дэлгэрэнгүй мэдээлэл',exact=True).click(timeout=15000)
                panel = detail.locator('[id$="panel-OrganzationInfo"]')
                await panel.wait_for(timeout=15000)
                await detail.wait_for_function("document.querySelector('[id$=\"panel-OrganzationInfo\"]')?.innerText.includes('Байгууллагын регистрийн дугаар')",timeout=10000)
                details = await panel.inner_text()
                reports = await detail.locator('table tbody tr td:nth-child(2)').all_text_contents()
                reports = list(dict.fromkeys(t.strip() for t in reports if len(t.strip())>5))[:40]
                fields=profile_fields(name,detail.url,details,reports)
                if query.isdigit() and fields.get('registration') != query:
                    await detail.close()
                    continue
                html=(await detail.content()).encode()
                if len(html)>5_000_000:
                    raise ConnectorError('Байгууллагын хуудас хэмжээнээс хэтэрсэн.')
                digest=hashlib.sha256(html).hexdigest()
                records.append({'url':detail.url,'format':'organization_profile','row_index':0,'row':fields,
                                'extraction_method':'browser_form','content_sha256':digest})
                pages.append({'url':detail.url,'status':'read','rows_read':1,'matched_rows':1,'extraction_method':'browser_form'})
                await detail.close()
            except Exception as exc:
                pages.append({'url':URL,'status':'detail_failed','error':type(exc).__name__})
        return {'status':'partial' if len(names)>limit or any(p['status']!='read' for p in pages) else 'bounded_complete',
                'records':records,'pages':pages,'total_organization_matches':len(names),
                'note':f'Байгууллагын жинхэнэ хайлтын маягт ашигласан. {len(names)} таарсан байгууллагаас {len(records)} дэлгэрэнгүйг уншсан. Тайлангийн жагсаалт нь гүйлгээний дүн биш.'}
    return await render_public_page(URL,'shilendans.gov.mn',allowed,interaction=interact)
