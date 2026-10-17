RAINY TRANSPARENCY — Монголын нээлттэй мэдээллийн платформ

## Эх сурвалжийн интеграцийн төлөв
- Төрийн худалдан авах ажиллагааны REST API: https://opendata.tender.gov.mn/ — invitation, plan, suppliers endpoint adapter болон админ sync API нэмэгдсэн. Бодит ажиллуулахын тулд албан ёсны Bearer token шаардлагатай.
- Шилэн данс: https://shilendans.gov.mn/openData — төсөв, гүйцэтгэл, хөрөнгө оруулалт, орлого/зарлагын тайлангийн ангилал баталгаажсан; нийтэд нээлттэй API endpoint/schema баталгаажаагүй.
- Засгийн газрын Open Data: https://opendata.gov.mn/ — dataset бүрийн endpoint, лиценз, schema нягтлах шаардлагатай.
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