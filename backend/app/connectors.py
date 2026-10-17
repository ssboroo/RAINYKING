"""Official-source adapters. Only documented/authorized APIs are called; no login scraping."""
import httpx
from .config import settings
TENDER_API = "https://opendata.tender.gov.mn/api"
class ConnectorError(RuntimeError): pass
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
      {"code":"tender_official","name":"Төрийн худалдан авах ажиллагааны REST API","kind":"official_api","docs":"https://opendata.tender.gov.mn/","base_url":TENDER_API,"auth":"Bearer token from procurement authority","automated":True,"required_secret":"TENDER_API_TOKEN","datasets":["invitation","plan","suppliers","committee"],"note":"Токен шаардлагатай; committee endpoint нь хувь хүний мэдээлэл агуулж болох тул тусгай зөвшөөрөлгүйгээр импортлохгүй."},
      {"code":"shilendans","name":"Шилэн данс нээлттэй өгөгдөл","kind":"open_portal","docs":"https://shilendans.gov.mn/openData","base_url":"https://shilendans.gov.mn/openData","auth":"Public portal; machine-readable endpoint/API schema not verified","automated":False,"required_secret":None,"datasets":["budget","budget_execution","investment","expense_transactions","income_transactions","SOE_targets"],"note":"Албан ёсны портал дээр өгөгдлийн маягтууд нийтлэгдсэн. Баталгаатай API endpoint/schema олдоогүй."},
      {"code":"government_opendata","name":"Засгийн газрын нээлттэй өгөгдлийн портал","kind":"open_data_catalog","docs":"https://opendata.gov.mn/","base_url":"https://opendata.gov.mn/","auth":"Dataset-specific","automated":False,"required_secret":None,"datasets":["dataset_catalog","organization_datasets"],"note":"Dataset тус бүрийн API/download холбоос, лиценз, schema-г нягталсны дараа холбох."},
      {"code":"opendata_mn","name":"OpenData.mn","kind":"open_data_catalog","docs":"https://opendata.mn/","base_url":"https://opendata.mn/","auth":"Dataset-specific","automated":False,"required_secret":None,"datasets":["public_datasets"],"note":"Портал REST API боломж дурдсан боловч нийтэд ил API endpoint/schema тодорхой бус."},
      {"code":"audit","name":"Төрийн аудитын тайлангийн портал","kind":"portal","docs":"https://open.audit.mn/","base_url":"https://open.audit.mn/","auth":"Portal login shown","automated":False,"required_secret":None,"datasets":["audit_reports"],"note":"Нээлттэй API баримт олдоогүй; нэвтрэлтийг тойрохгүй."},
      {"code":"courts","name":"Шүүхийн шийдвэрийн цахим сан","kind":"portal","docs":"https://shuukh.mn/","base_url":"https://shuukh.mn/","auth":"Public portal; API schema not verified","automated":False,"required_secret":None,"datasets":["published_decisions"],"note":"Нийтийн шийдвэр, хувийн мэдээллийн хамгаалалт, таталтын нөхцөлийг шалгана."},
      {"code":"legalinfo","name":"Эрх зүйн мэдээллийн нэгдсэн систем","kind":"portal","docs":"https://legalinfo.mn/","base_url":"https://legalinfo.mn/","auth":"Public portal; API schema not verified","automated":False,"required_secret":None,"datasets":["laws","regulations","government_resolutions"],"note":"Албан API endpoint/schema баталгаажаагүй."},
      {"code":"company_registry","name":"Хуулийн этгээдийн улсын бүртгэлийн мэдээлэл","kind":"dataset","docs":"https://opendata.gov.mn/","base_url":"https://opendata.gov.mn/","auth":"Dataset-specific; access approval may apply","automated":False,"required_secret":None,"datasets":["legal_entities","registration_identifiers"],"note":"Өмчлөл, эцсийн өмчлөгчийн мэдээлэлд тусгай эрх, хууль ёсны үндэслэл шаардагдаж болно."}
    ]
