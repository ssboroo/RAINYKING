"""Official-source adapters. Only documented/authorized APIs are called; no login scraping."""
import httpx, csv, io, json, hashlib, asyncio
from datetime import datetime, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from pypdf import PdfReader
from urllib.parse import urlparse
from .config import settings
TENDER_API = "https://opendata.tender.gov.mn/api"
GOV_CKAN_API = "https://opendata.gov.mn/api/3/action"
async def public_json(url: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0), follow_redirects=True, headers={"User-Agent":"RAINY-TRANSPARENCY/1.0"}) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise ConnectorError(f"Нээлттэй өгөгдлийн хүсэлт амжилтгүй: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise ConnectorError("Эх сурвалж JSON бус хариу буцаалаа.") from exc

async def ckan_package_search(q: str = "*", start: int = 0, rows: int = 100) -> dict:
    result = await public_json(GOV_CKAN_API + "/package_search", {"q":q,"start":start,"rows":min(max(rows,1),100)})
    if not result.get("success"): raise ConnectorError("OpenData.gov.mn CKAN package_search амжилтгүй.")
    return result.get("result",{})

async def ckan_package_show(package_id: str) -> dict:
    result = await public_json(GOV_CKAN_API + "/package_show", {"id":package_id})
    if not result.get("success"): raise ConnectorError("OpenData.gov.mn CKAN package_show амжилтгүй.")
    return result.get("result",{})

async def fetch_public_resource(url: str, max_bytes: int = 5_000_000, max_rows: int = 500) -> list[dict]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or not (parsed.hostname == "opendata.gov.mn" or parsed.hostname.endswith(".opendata.gov.mn")):
        raise ConnectorError("Зөвхөн opendata.gov.mn домэйны HTTPS resource зөвшөөрнө.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0), follow_redirects=True, headers={"User-Agent":"RAINY-TRANSPARENCY/1.0"}) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks=[]; size=0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes: raise ConnectorError("Resource хэмжээний хязгаараас их байна.")
                    chunks.append(chunk)
                content_type=response.headers.get("content-type","").lower()
                raw=b"".join(chunks)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"Resource татахад алдаа: {type(exc).__name__}") from exc
    text=raw.decode("utf-8-sig",errors="replace")
    if "json" in content_type or url.lower().split("?")[0].endswith(".json"):
        obj=json.loads(text)
        if isinstance(obj,list): return [x if isinstance(x,dict) else {"value":x} for x in obj[:max_rows]]
        if isinstance(obj,dict):
            for key in ("data","records","results","result"):
                if isinstance(obj.get(key),list): return [x if isinstance(x,dict) else {"value":x} for x in obj[key][:max_rows]]
            return [obj]
    if "csv" in content_type or url.lower().split("?")[0].endswith(".csv"):
        return list(csv.DictReader(io.StringIO(text)))[:max_rows]
    raise ConnectorError("Resource CSV/JSON биш байна; тохирох parser шаардлагатай.")

class ConnectorError(RuntimeError): pass

PUBLIC_HOSTS = {"opendata.gov.mn","www.opendata.gov.mn","shilendans.gov.mn","www.shilendans.gov.mn","open.audit.mn","iaac.mn","www.iaac.mn","shuukh.mn","www.shuukh.mn","legalinfo.mn","www.legalinfo.mn","opendata.mn","www.opendata.mn","gov.opendata.mn","www.gov.opendata.mn","opendata.tender.gov.mn","tender.gov.mn","www.tender.gov.mn"}

def validate_public_url(url: str) -> str:
    parsed=urlparse(url)
    host=(parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme!="https" or host not in PUBLIC_HOSTS or parsed.username or parsed.password:
        raise ConnectorError("Зөвхөн урьдчилан зөвшөөрсөн нийтийн домэйны HTTPS URL ашиглана.")
    return host

async def fetch_registered_public_resource(url: str, max_bytes: int=5_000_000, max_rows: int=500) -> tuple[str,list[dict],dict]:
    validate_public_url(url)
    last_error=None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0,connect=10.0),follow_redirects=False,headers={"User-Agent":"RAINY-TRANSPARENCY/1.0","Accept":"*/*"}) as client:
                response=await client.get(url)
                if response.status_code in (301,302,303,307,308):
                    loc=response.headers.get("location")
                    if not loc: raise ConnectorError("Redirect Location байхгүй.")
                    redirected=urljoin(url,loc); validate_public_url(redirected)
                    response=await client.get(redirected)
                response.raise_for_status()
                raw=response.content
                if len(raw)>min(max_bytes,5_000_000): raise ConnectorError("Файлын хэмжээ 5 MB хязгаараас их.")
                content_type=response.headers.get("content-type","").lower()
                final_url=str(response.url)
            fmt,rows=parse_public_payload(raw,content_type,final_url,max_rows)
            return fmt,rows,{"final_url":final_url,"content_type":content_type,"bytes_downloaded":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}
        except ConnectorError: raise
        except (httpx.HTTPError,ValueError) as exc:
            last_error=exc
            if attempt<2: await asyncio.sleep(.5*(2**attempt))
    raise ConnectorError("Таталт 3 удаа амжилтгүй: "+type(last_error).__name__)

def parse_public_payload(raw: bytes, content_type: str, url: str, max_rows: int=500) -> tuple[str,list[dict]]:
    clean=url.lower().split("?")[0]
    text=raw.decode("utf-8-sig",errors="replace")
    if "json" in content_type or clean.endswith(".json"):
        obj=json.loads(text)
        if isinstance(obj,list): rows=obj
        elif isinstance(obj,dict): rows=next((obj[k] for k in ("data","records","results","result","items") if isinstance(obj.get(k),list)),[obj])
        else: rows=[{"value":obj}]
        return "json",[r if isinstance(r,dict) else {"value":r} for r in rows[:max_rows]]
    if "csv" in content_type or clean.endswith(".csv"):
        try: dialect=csv.Sniffer().sniff(text[:4096],delimiters=",;\\t|")
        except csv.Error: dialect=csv.excel
        return "csv",list(csv.DictReader(io.StringIO(text),dialect=dialect))[:max_rows]
    if "spreadsheetml" in content_type or "excel" in content_type or clean.endswith((".xlsx",".xlsm")):
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True); sheet=book.active
        iterator=sheet.iter_rows(values_only=True); header=next(iterator,())
        names=[str(v).strip() if v is not None else "column_"+str(i+1) for i,v in enumerate(header)]
        rows=[]
        for values in iterator:
            if len(rows)>=max_rows: break
            if any(v is not None for v in values):
                rows.append({names[i] if i<len(names) else "column_"+str(i+1):(v.isoformat() if hasattr(v,"isoformat") else v) for i,v in enumerate(values)})
        return "xlsx",rows
    if "pdf" in content_type or clean.endswith(".pdf"):
        reader=PdfReader(io.BytesIO(raw),strict=False); rows=[]
        for i,page in enumerate(reader.pages[:max_rows],1):
            page_text=(page.extract_text() or "").strip()
            if page_text: rows.append({"page_number":i,"text":page_text[:20000]})
        return "pdf",rows
    if "html" in content_type or clean.endswith(("/",".html",".htm")) or "<html" in text[:1000].lower():
        soup=BeautifulSoup(text,"html.parser")
        for tag in soup(["script","style","noscript"]): tag.decompose()
        rows=[]
        for ti,table in enumerate(soup.find_all("table")[:20],1):
            trs=table.find_all("tr")
            if not trs: continue
            headers=[c.get_text(" ",strip=True) or "column_"+str(i+1) for i,c in enumerate(trs[0].find_all(["th","td"]))]
            for tr in trs[1:]:
                cells=[c.get_text(" ",strip=True) for c in tr.find_all(["th","td"])]
                if cells: rows.append({"table_index":ti,**{headers[i] if i<len(headers) else "column_"+str(i+1):v for i,v in enumerate(cells)}})
                if len(rows)>=max_rows: break
            if len(rows)>=max_rows: break
        if rows:return "html_table",rows
        title=soup.title.get_text(" ",strip=True) if soup.title else ""
        body=soup.get_text(" ",strip=True)
        return "html_text",[{"title":title,"text":body[:20000]}] if body else []
    raise ConnectorError("Формат танигдсангүй: API JSON, CSV, XLSX, PDF эсвэл HTML шаардлагатай.")

async def inspect_public_source(url: str) -> dict:
    fmt,rows,meta=await fetch_registered_public_resource(url,max_bytes=5_000_000,max_rows=3)
    return {"url":url,"format":fmt,"sample_records":len(rows),"sample":rows[:3],"checked_at":datetime.now(timezone.utc).isoformat(),**meta}


async def tender_post(path: str, params: dict) -> dict:
    if not settings.tender_bearer_token:
        raise ConnectorError("TENDER_API_TOKEN тохируулаагүй. Төрийн худалдан авах ажиллагааны газраас API эрх авна уу.")
    headers={"Authorization": f"Bearer {settings.tender_bearer_token}", "Content-Type":"application/x-www-form-urlencoded", "Accept":"application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False) as client:
            response=await client.post(TENDER_API + path, data=params, headers=headers)
    except httpx.HTTPError as exc: raise ConnectorError(f"Тендер API сүлжээний алдаа: {type(exc).__name__}") from exc
    if response.status_code in (401,403,404): raise ConnectorError(f"Тендер API хандалт амжилтгүй (HTTP {response.status_code}); токен болон endpoint эрхийг шалгана уу.")
    if response.status_code >= 400: raise ConnectorError(f"Тендер API HTTP {response.status_code}")
    try: body=response.json()
    except ValueError as exc: raise ConnectorError("Тендер API JSON бус хариу буцаалаа.") from exc
    if not isinstance(body,dict) or not isinstance(body.get("data",[]),list): raise ConnectorError("Тендер API-ийн хариу хүлээгдсэн data[] бүтэцтэй биш байна.")
    if body.get("return_code") not in (None,200,"200"): raise ConnectorError("Тендер API: "+str(body.get("message","unknown API error"))[:300])
    return body
async def tender_invitations(page:int=1, per_page:int=100, year:int|None=None)->dict:
    p={"current_page":page,"per_page":per_page}
    if year is not None:p["tenderYear"]=year
    return await tender_post("/invitation",p)
async def tender_plans(page:int=1, per_page:int=100, year:int|None=None)->dict:
    p={"current_page":page,"per_page":per_page}
    if year is not None:p["tenderYear"]=year
    return await tender_post("/plan",p)
async def tender_suppliers(invitation_id:int, page:int=1, per_page:int=100)->dict:
    return await tender_post(f"/invitation/{invitation_id}/suppliers",{"current_page":page,"per_page":per_page})
def connector_catalog():
    return [
      {"code":"tender_official","name":"Төрийн худалдан авах ажиллагааны REST API","kind":"official_api","docs":"https://opendata.tender.gov.mn/","base_url":TENDER_API,"auth":"Bearer token from procurement authority","automated":True,"required_secret":"TENDER_BEARER_TOKEN","datasets":["invitation","plan","suppliers","committee"],"note":"Токен шаардлагатай; committee endpoint нь хувь хүний мэдээлэл агуулж болох тул тусгай зөвшөөрөлгүйгээр импортлохгүй."},
      {"code":"shilendans","name":"Шилэн данс нээлттэй өгөгдөл","kind":"open_portal","docs":"https://shilendans.gov.mn/openData","base_url":"https://shilendans.gov.mn/openData","auth":"Public portal; machine-readable endpoint/API schema not verified","automated":False,"required_secret":None,"datasets":["budget","budget_execution","investment","expense_transactions","income_transactions","SOE_targets"],"note":"Албан ёсны портал дээр өгөгдлийн маягтууд нийтлэгдсэн. Баталгаатай API endpoint/schema олдоогүй."},
      {"code":"government_opendata","name":"Засгийн газрын нээлттэй өгөгдлийн портал","kind":"ckan_public_api","docs":"https://opendata.gov.mn/","base_url":GOV_CKAN_API,"auth":"Public CKAN API; resource/license-specific","automated":True,"required_secret":None,"datasets":["dataset_catalog","dataset_resources","public_csv_json_records"],"note":"CKAN package_search/package_show нийтийн API; CSV/JSON resource таталт 5 MB болон 500 мөрийн хамгаалалттай."},
      {"code":"opendata_mn","name":"OpenData.mn","kind":"open_data_catalog","docs":"https://opendata.mn/","base_url":"https://opendata.mn/","auth":"Dataset-specific","automated":False,"required_secret":None,"datasets":["public_datasets"],"note":"Портал REST API боломж дурдсан боловч нийтэд ил API endpoint/schema тодорхой бус."},
      {"code":"audit","name":"Төрийн аудитын тайлангийн портал","kind":"portal","docs":"https://open.audit.mn/","base_url":"https://open.audit.mn/","auth":"Portal login shown","automated":False,"required_secret":None,"datasets":["audit_reports"],"note":"Нээлттэй API баримт олдоогүй; нэвтрэлтийг тойрохгүй."},
      {"code":"courts","name":"Шүүхийн шийдвэрийн цахим сан","kind":"portal","docs":"https://shuukh.mn/","base_url":"https://shuukh.mn/","auth":"Public portal; API schema not verified","automated":False,"required_secret":None,"datasets":["published_decisions"],"note":"Нийтийн шийдвэр, хувийн мэдээллийн хамгаалалт, таталтын нөхцөлийг шалгана."},
      {"code":"legalinfo","name":"Эрх зүйн мэдээллийн нэгдсэн систем","kind":"portal","docs":"https://legalinfo.mn/","base_url":"https://legalinfo.mn/","auth":"Public portal; API schema not verified","automated":False,"required_secret":None,"datasets":["laws","regulations","government_resolutions"],"note":"Албан API endpoint/schema баталгаажаагүй."},
      {"code":"company_registry","name":"Хуулийн этгээдийн улсын бүртгэлийн мэдээлэл","kind":"dataset","docs":"https://opendata.gov.mn/","base_url":"https://opendata.gov.mn/","auth":"Dataset-specific; access approval may apply","automated":False,"required_secret":None,"datasets":["legal_entities","registration_identifiers"],"note":"Өмчлөл, эцсийн өмчлөгчийн мэдээлэлд тусгай эрх, хууль ёсны үндэслэл шаардагдаж болно."}
    ]
