RAINY TRANSPARENCY — Монголын нээлттэй мэдээллийн платформ

## Эх сурвалжийн интеграцийн төлөв
- Төрийн худалдан авах ажиллагааны REST API: https://opendata.tender.gov.mn/ — invitation, plan, suppliers endpoint adapter болон админ sync API нэмэгдсэн. Бодит ажиллуулахын тулд албан ёсны Bearer token шаардлагатай.
- Шилэн данс: https://shilendans.gov.mn/openData — төсөв, гүйцэтгэл, хөрөнгө оруулалт, орлого/зарлагын тайлангийн ангилал баталгаажсан; нийтэд нээлттэй API endpoint/schema баталгаажаагүй.
- Засгийн газрын Open Data: https://opendata.gov.mn/ — CKAN `package_search`/`package_show` нийтийн API-аар каталог татах, CSV/JSON resource-ийг хязгаартай хадгалах endpoint нэмэгдсэн.
- OpenData.mn: https://opendata.mn/ — портал REST API дурдсан боловч endpoint/schema баталгаажуулах шаардлагатай.
- Аудит: https://open.audit.mn/ — нийтэд нээлттэй API баримт олдоогүй.
- Шүүх: https://shuukh.mn/ — API schema баталгаажаагүй.
- Эрх зүй: https://legalinfo.mn/ — албан API schema баталгаажаагүй.
- Хуулийн этгээдийн бүртгэл/өмчлөл: dataset болон эрхийн нөхцөлийг баталгаажуулах шаардлагатай.

Procurement API: POST /api/invitation, POST /api/plan, POST /api/invitation/{invitationId}/suppliers. Params current_page, per_page (1–100), optional tenderYear; Authorization Bearer token. Token авах талаар: it@procurement.gov.mn, 7599-3333. Docs: https://opendata.tender.gov.mn/.

Set TENDER_BEARER_TOKEN as deployment secret. Never commit secrets. Admin endpoints require x-api-key matching ADMIN_API_KEY:
- POST /api/v1/admin/sync/tenders JSON {"year":2026,"max_pages":2,"per_page":100}
- POST /api/v1/admin/sync/tender-plans JSON {"year":2026,"max_pages":1,"per_page":100} (normalized upsert)
- POST /api/v1/admin/sync/tender-suppliers JSON {"invitation_id":123456,"max_pages":1,"per_page":100} (admin-only import; check data-use terms before public display)
- POST /api/v1/admin/check/tender-suppliers JSON {"invitation_id":123456,"per_page":100}
- GET /api/v1/integrations and /api/v1/source-status list source endpoints, requirements and sync status.

No portal login is bypassed and no HTML scraping is presented as an official API. Tender plans and supplier responses now have normalized tables; contract documents and financial transactions still need verified source schemas and dedicated connectors. Local: cp .env.example .env; set secrets; docker compose up --build -d. API docs at http://localhost:8000/docs.

## Нээлттэй өгөгдлийн таталт

Засгийн газрын Open Data portal-ийн CKAN API: `https://opendata.gov.mn/api/3/action`.
- `POST /api/v1/admin/sync/opendata-catalog?q=*&max_pages=2&per_page=100` — каталогийн metadata-г хадгална.
- `POST /api/v1/admin/sync/opendata-resource` JSON `{"dataset_id":"CKAN_PACKAGE_ID","resource_id":null,"max_rows":500,"max_bytes":5000000}` — CSV/JSON resource-ийн мөрүүдийг хадгална. resource_id өгөхгүй бол эхний CSV/JSON resource сонгоно.
- Resource зөвхөн opendata.gov.mn домэйны HTTPS хаягтай, 5 MB хүртэл, 500 мөр хүртэл байна.
- Шилэн данс, аудит, шүүх, legalinfo, OpenData.mn-ийн хувьд албан ёсны API эсвэл download resource-ийн формат бүрийг баталгаажуулах хүртэл порталын холбоос/төлөв бүртгэнэ. Нэвтрэлтийг тойрохгүй.


## Эх сурвалж илрүүлэх, олон формат татах, мониторинг
- POST /api/v1/admin/sources/inspect — бүртгэлтэй эх сурвалжийн нийтийн HTTPS URL-ийн формат, MIME төрөл, хэмжээ, SHA-256, жишээг шалгана.
- POST /api/v1/admin/sources/sync-public — JSON/CSV/XLSX/PDF/нийтийн HTML хүснэгт болон текст татаж, raw + стандартчилсан мөрийг хадгална.
- GET /api/v1/admin/source-metrics — эх сурвалж тус бүрийн нийт бүртгэл, сүүлийн амжилт, алдааг харуулна.
- GET /api/v1/admin/sync-runs — таталтын түүх, тоо, хэмжээ, MIME, хэш, алдааг харуулна.
- HTTP сүлжээний алдаанд 3 удаа хүртэл retry/backoff хийнэ. Зөвшөөрсөн домэйн, HTTPS, 5 MB болон 500 мөрийн хязгаар үйлчилнэ. Login/CAPTCHA тойрохгүй.
- Монгол/англи толгой нэрээс нэр, регистр, байгууллага, тендер/гэрээний дугаар, төсөв, огноог стандартчилна; raw эх өгөгдлийг хамт хадгална. Ижил нэрээр шууд нэгтгэхгүй.
- Хязгаарлалт: PDF нь текстэн PDF байх шаардлагатай (OCR байхгүй); XLSX active worksheet-ийн эхний мөрийг header гэж үзнэ; сайт бүрийн API/schema өөр тул эх сурвалж тус бүр дээр URL-ийг эхлээд inspect хийж баталгаажуулна.

- Optional periodic worker: set AUTO_SYNC_SOURCES to a JSON list such as [{"source_code":"shilendans","url":"https://shilendans.gov.mn/openData"}], then run `docker compose --profile scheduler up -d --build`. AUTO_SYNC_INTERVAL_MINUTES defaults to 360. Empty configuration means idle; only choose stable, public dataset URLs, not a login page or a general portal homepage.


## Дараагийн шат: өгөгдлийн мөр ба entity resolution
- Админ: GET `/api/v1/admin/source-records?source_code=...` нь эх холбоос, raw өгөгдөл, стандартчилсан талбар, SHA-256 хэшийг харуулна.
- Админ: GET `/api/v1/admin/entity-resolution/candidates?threshold=0.88` нь нэр төстэй компани/байгууллагын боломжит давхардлыг санал болгоно. Энэ нь зөвхөн review queue бөгөөд автоматаар нэгтгэхгүй.
- Админ: POST `/api/v1/admin/relationships` холбоосыг нотлох SourceRecord ID-тай үүсгэнэ. POST `/api/v1/admin/relationships/{id}/review` холбоосыг verified/rejected/pending_review болгож, audit log-д тэмдэглэнэ.
- GET `/api/v1/relationships?verification_status=verified` зөвхөн баталгаажсан холбоосуудыг өгнө.
- Өгөгдлийн мөрийн тогтвортой түлхүүр нь одоо мөрийн дугаараас хамаарахгүй: хүчтэй ID (регистр/тендер/гэрээ) эсвэл canonical мөрийн хэш ашиглана.
