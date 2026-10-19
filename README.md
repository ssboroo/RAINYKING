RAINY TRANSPARENCY — Монголын нээлттэй мэдээллийн платформ

## Одоогийн холболтын баталгаажуулалт
2026-10-17: өмнө нь CKAN гэж тэмдэглэсэн `opendata.gov.mn/api/3/action` холболтын одоогийн endpoint/schema баталгаажаагүй. CKAN adapter код байгаа нь live портал CKAN ашигладаг гэсэн баталгаа биш; каталогт `unverified_ckan_adapter` гэж харуулна. Доорх CKAN маршрут нь compatibility adapter бөгөөд бодит хариуг шалгахаас өмнө бэлэн интеграц гэж үзэхгүй. Улсын бүртгэлийн нийтийн портал: https://opendata.burtgel.gov.mn/.


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

## Тендерийн сангийн хайлт ба хуудаслалт
- `GET /api/v1/tender-explorer?q=&budget=any&competition=any&page=1&page_size=30` бүх тендерийн сангаас шүүнэ.
- `budget`: `any`, `known`, `large` (100 сая ₮ ба түүнээс дээш), `small` (100 сая ₮-өөс доош).
- `competition`: `any`, `multiple` (2+ санал), `single`, `unknown`.
- Хариу: `items`, `total`, `page`, `page_size`, `has_next`. Нэг хуудсанд 1–100 бүртгэл; шинэ бүртгэл эхэнд байна.
- Вебийн «Тендерийн сан» бүх сангийн шүүлт, өмнөх/дараах хуудас, ачаалалт ба дахин оролдох төлөвтэй. Хуучин `/api/v1/tenders` массив хариугаа хадгална.
- SQLite хайлтад Монгол үсгийн том/жижиг бичлэгийг дэмжинэ; `%`, `_` тэмдэгтүүдийг хайлтын энгийн текст гэж үзнэ.

## Шийдэх асуудал: худалдан авалтын ил тод байдал
«Зарцуулалтын хяналт» хэсэг `/api/v1/procurement-analysis` API-аар импортолсон сангийн төсөв, нэг саналтай тендерийн хувь, саналын тоо дутуу бүртгэл, захиалагч болон регистртэй шалгарсан компанийн төвлөрлийг харуулна. Байгууллага/компаниас эх баримтын холбоосын шинжилгээ рүү орно.

Хувийн суурь тоо, мэдээлэлгүй талбар, хамрах хүрээг ил харуулна. Тендерийн төсвийг бодит төлбөр, гэрээний дүн эсвэл компанийн орлого гэж тайлбарлахгүй. Ижил нэрээр өмчлөл эсвэл ашиг сонирхлын зөрчил тогтоохгүй. Дараагийн интеграцийн чиглэл нь төсвийн гүйлгээ → гэрээ/гүйцэтгэл → аудит; тусгай сан, газар/лиценз, ХОМ, өмчлөл, улс төрийн санхүүжилтийн өгөгдөлд эх сурвалж, ашиглах нөхцөл, ID/schema тус бүрийг баталгаажуулна.

## Байгууллага, регистр, эзэмшигчийн дэлгэрэнгүй шүүлт
`GET /api/v1/record-search` нь `q`, `company_name`, `organization_name`, `registration`, `owner_name`, `source_code`, `offset`, `limit` нөхцөлтэй. Нөхцөлүүд AND; регистр яг таарна, нэрийн үгс нэг нэрийн утгад таарна. `q` нь хадгалсан raw/normalized баримтын бүх утгаас хайна. Эх сурвалж, URL, SHA-256, олдсон талбарын замыг хариунд өгнө. Одоогийн хувилбар сонгосон эх сурвалжийн баримтуудыг streaming scan хийдэг тул их хэмжээний санд тусдаа индекс шаардлагатай.

`owner_name` / `owner_full_name`, эзэмшигчийн овог нэр, nested `owners` / `shareholders` доторх surname/first_name зэрэг ил тод талбаруудаас эзэмшигчийн нэрийг олно. Эзэмшигчийн регистрийг компанийн регистртэй андуурахгүй. Графийн `owner_mention` зангилаа нь тухайн баримтын мэдэгдэл; ижил нэрээр хүнийг нэгтгэхгүй. Компанийн регистр таарсан эх баримтуудыг графтай холбоно. Граф гүн болон салааллын хязгаартай тул бүх боломжит холбоог бүрэн харуулсан гэж үзэхгүй. Эзэмшил, ашиг сонирхлын зөрчил эсвэл гэм бурууг автоматаар тогтоохгүй.

## Нэгдсэн тайлан ба API-гүй веб илрүүлэлт
- `GET /api/v1/unified-report?q=...&registration=...` — дор хаяж нэг нөхцөлтэй; эх сурвалж+external_id бүрийн сүүлийн хувилбар, exact ID холбоостой тендер, эзэмшигчийн эх баримтын мэдэгдэл, эрсдэлийн дохио, хамрах хүрээ, аргачлал/хязгаарыг нэгтгэнэ. Хуучин хувилбар, татгалзсан дохиог дүгнэлтийн тоонд оруулахгүй. Төсвийг валютаар тусад нь харуулна. Нүүр хуудас тайлангийн интерфейстэй, JSON татах болон хэвлэх/PDF боломжтой.
- Админ `POST /api/v1/admin/sources/discover-web`: `{"source_codes":["company_registry","shilendans","iaac"],"q":"компанийн нэр","max_pages":5,"max_depth":2}`. Header: `x-api-key`.
- API шаардлагагүй: robots.txt → нийтийн HTML → ажиглагдсан GET хайлтын маягт → дотоод холбоос → HTML хүснэгт/текст, текстэн PDF, XLSX, CSV, JSON. Нэг эх сурвалжид 1–10 хуудас, 0–3 түвшин, 5 MB/баримт. URL/redirect бүрийг allowlist ба эх сурвалжийн домэйнээр шалгана. Нэг source тутамд завсарлага, robots crawl-delay үйлчилнэ. Нэвтрэлт/CAPTCHA тойрохгүй.
- Веб интерфейсийн «API-гүй нийтийн вебээс баримт хайж импортлох» хэсэгт эх сурвалжууд сонгож, админ түлхүүрээр таталт эхлүүлнэ. Түлхүүр browser storage-д хадгалахгүй, ажиллагааны дараа арилгана. Хадгалсан мөрүүд шууд нэгдсэн хайлт/тайланд ашиглагдана; тайлангаа дахин үүсгэнэ.
- Сайт бүрийн хүрээ, амжилт/алдаа, robots хориг, JavaScript шаардлагатай байж болох хоосон HTML, OCR шаардлагатай зурагт PDF-ийг тусад нь харуулна. Ерөнхий crawler нь хязгаартай discovery; порталын бүх хайлт, JS app, зурагт PDF-г бүрэн дэмждэг гэж үзэхгүй. Том сайтын хувьд тусгай DOM/browser adapter, OCR worker, индекс ба background queue дараагийн ажил.

## Railway: нэг сервис, нэг домэйн
Репозиторийн үндсэн `Dockerfile` frontend + FastAPI + nginx-ийг нэг сервисээр ажиллуулна. `railway.json` build, `/health` шалгалт, restart policy тохируулна. Railway service-ийн root directory `/` байна. Нийтийн домэйн дээр `/api/*` ба `/health` backend руу, бусад зам frontend руу очно; browser API дуудлага same-origin тул `NEXT_PUBLIC_API_URL` шаардлагагүй.

- Persistent volume: `/data`; `DATABASE_URL=sqlite:////data/transparency.db` — нэг instance MVP-д. Volume-гүй SQLite production-д ашиглахгүй.
- PostgreSQL ашиглах бол `DATABASE_URL=${{Postgres.DATABASE_URL}}`; `postgres://` / `postgresql://` нь psycopg driver URL руу тохируулагдана. Олон instance/их ачаалалд PostgreSQL ба worker/queue шаардлагатай.
- `ADMIN_API_KEY`: Railway secret variable; `TENDER_BEARER_TOKEN`: албан API token байгаа үед л тохируулна.
- `PORT`: Railway автоматаар өгнө, nginx тэр порт дээр сонсоно. Backend 8000, frontend 3000 нь зөвхөн container дотор байна.
- Админ түлхүүр нь UI-аас харагдахгүй, crawler/import endpoint-үүд түүнгүйгээр ажиллахгүй. Ажиллуулахын тулд Railway Variables-оос эрхтэй админ түлхүүрээ ашиглана.
- Тусдаа frontend/backend deploy мөн боломжтой: root `/frontend`, `/backend`; frontend build үед нийтийн backend `NEXT_PUBLIC_API_URL`, backend дээр тохирох `ALLOWED_ORIGINS` өгнө. Backend Docker нь одоогоор 8000 порттой тул Railway domain-ийн target port 8000 сонгоно.

Баримт: https://docs.railway.com/config-as-code/reference болон https://docs.railway.com/deployments/monorepo . Root Docker build-д Next.js 15.5.28 ба npm lockfile ашиглана.

Railway-ийн энэ төслийн үндсэн хувилбар нь тусдаа PostgreSQL сервис, persistent volume ашиглана. SQLite нь зөвхөн локал/MVP сонголт; улсын хэмжээний сангийн архитектур гэж үзэхгүй. Үндэсний түвшний нэвтрүүлэлтээс өмнө full-text/талбарын индекс, background queue, versioned migrations, backup/restore туршилт, дүрд суурилсан админ нэвтрэлт, ажиглалт ба ачааллын шалгалт шаардлагатай. Одоогийн crawler/report нь хязгаартай эхний ажиллагаатай хувилбар.

## Хяналтын төв ба өргөтгөсөн баримт уншилт
Нүүр хуудас бодит сангийн үзүүлэлт, эх сурвалжийн матриц, сүүлийн таталт, судалгаа/тайлангийн шууд үйлдэлтэй. Desktop/tablet/mobile layout, keyboard focus, танигдах label-тай navigation ашиглана.

`ENABLE_BROWSER_EXTRACTION=true` үед богино JavaScript app shell-д Chromium дүрслэл ашиглаж, ачаалсан DOM хүснэгт/текстийг уншина. Browser session бүр тусгаарлагдсан; зөвхөн тухайн эх сурвалжийн HTTPS домэйн/дэд домэйн, нийтийн DNS хаяг, robots нөхцөлтэй хүсэлтүүдийг зөвшөөрнө. Нэвтрэлт, CAPTCHA, bot challenge тойрохгүй; дүрслэл нэг зэрэг 1, нийт 40 секундийн хязгаартай.

`ENABLE_PDF_OCR=true` үед текстгүй PDF-ийн эхний 10 хуудсыг Poppler + Tesseract `mon+eng` ашиглан танина. OCR мөрүүдийг танилтын алдаа байж болохыг тэмдэглэж хадгална. Root Docker image эдгээр runtime-ийг суулгана; тусдаа backend Docker ашиглавал өөрөө суулгах эсвэл flag-уудыг унтраана. Browser renderer нь portal-specific filter navigation-ийг автоматаар бүрэн тайлж чадахгүй; татсан үр дүнгийн page/depth хязгаар хэвээр.

### API түлхүүргүй нийтийн веб таталт

Бүх каталогийн эх сурвалж `default_acquisition=public_web` горимтой. Тендерийн хувьд токентой REST API-ийн оронд `https://www.tender.gov.mn/` нийтийн порталаас эхэлнэ. Албан API нь токен байгаа үед тусдаа боломж хэвээр байна.

Нэгдсэн тайлангийн веб импортод бүх эх сурвалж анхнаасаа сонгогдоно. Эх сурвалж бүрийн нийтийн жагсаалт/хайлтын URL-ийг оруулж болно (`start_urls` талбар); зөвхөн тухайн эх сурвалжийн зөвшөөрсөн HTTPS домэйныг хүлээн авна. HTML, ажиглагдсан GET маягт, JS дүрслэл, нийтийн файлууд, зурагт PDF-ийн OCR замыг ашиглана. Нэг ажил эх сурвалж бүрээс 5 хуудас, 2 түвшин уншина; энэ нь бүх санг бүрэн индексэлсэн гэсэн үг биш. Хаалт, robots, CAPTCHA, тусгай динамик маягтын хязгаар үр дүнд тусна. Гадаад API түлхүүр хэрэггүй; импорт ажиллуулахад платформын дотоод админ эрх шаардлагатай.

### Нийтийн шууд хайлт

Тайлангийн «Хайх ба тайлан гаргах» үйлдэл хадгалсан сангийн тайлан болон API/админ түлхүүргүй нийтийн веб хайлтыг зэрэг эхлүүлнэ. `GET /api/v1/public-web-search?source_code=courts&q=...` нь зөвхөн каталогийн эх сурвалж, 2–200 тэмдэгтийн асуулт хүлээн авна. Нэг эх сурвалжаас 3 хүртэл хуудас, 2 түвшин уншина; буцах эх сурвалжийн URL, хуудасны төлөв, таарсан хэсэг, extraction арга тодорхой байна. Үр дүнг DB-д бичихгүй, баталгаажсан ID холбоос эсвэл гэм буруугийн тайланд автоматаар нэгтгэхгүй. Импортын админ хамгаалалт хэвээр.

Сервер нэг дор 2 таталт ажиллуулж, 4 идэвхтэй ажлаас илүү хүсэлтийг busy төлөвөөр буцаана. Ижил хүсэлтүүд нэг таталт хуваалцаж, 10 минутын 128 хүртэл cache ашиглана. Тусгай динамик шүүлтүүр, robots, login, CAPTCHA, сүлжээний алдаа бүр тусдаа хязгаар; үр дүнгүйг бүх санд мэдээлэлгүй гэж тайлбарлахгүй.
