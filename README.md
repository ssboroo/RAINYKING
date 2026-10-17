RAINY TRANSPARENCY — Монголын нээлттэй мэдээллийн платформ

Албан ёсны эх сурвалжийн интеграцийн төлөв:

- Төрийн худалдан авах ажиллагааны REST API: https://opendata.tender.gov.mn/ — invitation, plan, suppliers endpoint adapter нэмэгдсэн. Бодит ажиллуулахын тулд эрх бүхий байгууллагаас Bearer token авах шаардлагатай.
- Шилэн данс: https://shilendans.gov.mn/openData — төсөв, гүйцэтгэл, хөрөнгө оруулалт, орлого/зарлагын тайлангийн ангилал баталгаажсан; нийтэд нээлттэй, баримтжуулсан API endpoint/schema одоогоор баталгаажаагүй.
- Засгийн газрын Open Data: https://opendata.gov.mn/ — dataset бүрийн API/download endpoint, лиценз, schema-г шалгах шаардлагатай.
- OpenData.mn: https://opendata.mn/ — портал REST API дурдсан боловч endpoint/schema баталгаажуулах шаардлагатай.
- Аудит: https://open.audit.mn/ — портал нэвтрэх хуудастай; нийтэд нээлттэй API баримт олдоогүй.
- Шүүх: https://shuukh.mn/ — нийтэд харагдах шийдвэрийн API schema баталгаажаагүй.
- Эрх зүй: https://legalinfo.mn/ — албан API schema баталгаажаагүй.
- Хуулийн этгээдийн бүртгэл/өмчлөл: dataset болон эрхийн нөхцөлийг баталгаажуулах шаардлагатай.

Procurement API endpoint: POST /api/invitation, POST /api/plan, POST /api/invitation/{invitationId}/suppliers. Params current_page, per_page (1–100), optional tenderYear; Authorization: Bearer token. Token хүсэх холбоо барих мэдээлэл: it@procurement.gov.mn, 7599-3333.

Set TENDER_API_TOKEN as deployment secret, never commit it. Admin endpoints require x-api-key: ADMIN_API_KEY:
- POST /api/v1/admin/sync/tenders JSON {"year":2026,"max_pages":2,"per_page":100}
- POST /api/v1/admin/sync/tender-plans JSON {"year":2026,"max_pages":1,"per_page":100} (read/check only; separate plan table not implemented)
- POST /api/v1/admin/check/tender-suppliers JSON {"invitation_id":123456,"per_page":100}
- GET /api/v1/integrations shows endpoints, access requirements and last sync status.

No portal login is bypassed and no HTML scraping is presented as an official API. The dashboard must not invent data. Plans, supplier bids, contracts and financial transactions need dedicated normalized tables before production analytics. See API docs at http://localhost:8000/docs after local setup.