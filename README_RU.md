# Visual Scan

Браузерный сканер документов с Canvas-предобработкой, клиентским и серверным OCR, опциональным AI-анализом и backend на FastAPI + SQLite.

Visual Scan принимает изображения, снимки с камеры и PDF. Изображение можно исправить прямо в браузере перед OCR, распознать через Tesseract.js или серверный Tesseract, проанализировать через OpenAI-compatible provider, отредактировать текст и сохранить результат в персональный архив пользователя.

· [English version](README.md)

## 📋 Оглавление

- [🚀 Быстрый запуск](#-быстрый-запуск)
- [📝 О проекте](#-о-проекте)
- [✨ Возможности](#-возможности)
- [🔄 Схема работы](#-схема-работы)
- [🔎 OCR-движки](#-ocr-движки)
- [🤖 AI-анализ](#-ai-анализ)
- [🔐 Авторизация и архив](#-авторизация-и-архив)
- [📄 Демо-документы](#-демо-документы)
- [🌐 Backend API](#-backend-api)
- [🧰 Стек технологий](#-стек-технологий)
- [🧪 Тесты и проверки](#-тесты-и-проверки)
- [📁 Структура проекта](#-структура-проекта)
- [⚠️ Примечания](#️-примечания)
- [🧑‍💻 Автор](#-автор)

## 🚀 Быстрый запуск

### Требования

- Python `3.11+`
- Tesseract `5` для серверного OCR
- современный браузер
- Node.js `18+` только для скриптов OCR-моделей и frontend-тестов
- OpenAI-compatible endpoint и API key только если нужен AI-анализ

В репозитории уже лежат Fast-модели английского и русского для Browser OCR. Для профиля по умолчанию ничего дополнительно скачивать не нужно.

### Клонирование и установка

#### Windows (Git Bash)

```bash
git clone https://01.tomorrow-school.ai/git/nyestaye/visual-scan
cd visual-scan

python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

#### macOS / Linux

```bash
git clone https://01.tomorrow-school.ai/git/nyestaye/visual-scan
cd visual-scan

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Установка системного Tesseract

Ubuntu / Debian:

```bash
sudo apt install tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    tesseract-ocr-deu \
    tesseract-ocr-fra \
    tesseract-ocr-spa
```

macOS:

```bash
brew install tesseract tesseract-lang
```

На Windows установите Tesseract 5 и добавьте его каталог в `PATH`. Если executable не находится через `PATH`, укажите полный путь в `backend/.env`:

```dotenv
VISUAL_SCAN_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Проверка установки:

```bash
tesseract --version
tesseract --list-langs
```

### Запуск backend

```bash
python -m uvicorn app.main:app --app-dir backend --reload
```

API доступен по адресам:

```text
http://localhost:8000
http://localhost:8000/docs
```

### Запуск frontend

Откройте второй терминал из корня репозитория:

```bash
python -m http.server 5500
```

Затем откройте:

```text
http://localhost:5500/frontend/index.html
```

Не открывайте `frontend/index.html` через `file://`: ES modules в браузере требуют HTTP origin.

### Первый запуск

Browser OCR работает без учётной записи. Server OCR, PDF OCR, AI-анализ, сохранение и архив **Scanned Results** требуют локальную учётную запись Visual Scan.

1. Откройте **Upload & Scan**.
2. Перетащите изображение, выберите файл, загрузите demo document или сделайте снимок с камеры.
3. При необходимости настройте rotation, deskew, crop, grayscale, threshold или invert.
4. Сразу запустите Browser OCR либо зарегистрируйтесь и используйте Server OCR.
5. Отредактируйте распознанный текст.
6. При необходимости запустите AI analysis.
7. Сохраните результат.
8. Откройте **Scanned Results** для поиска, фильтрации, просмотра, export или удаления сохранённых сканов.

Регистрация локальная и не требует внешнего аккаунта.

## 📝 О проекте

Visual Scan разделяет работу с документом в браузере и серверную обработку.

Изображения остаются интерактивными на frontend: исходная картинка загружается в Canvas, преобразования выполняются локально, а Browser OCR может работать без backend. Server OCR получает уже обработанное Canvas-изображение.

PDF использует отдельный серверный pipeline. Исходный PDF валидируется, проходит preflight, постранично рендерится через PDFium, при необходимости обрабатывается Pillow и последовательно распознаётся Tesseract.

Backend также предоставляет:

- опциональную OpenAI-compatible классификацию и summary документа;
- извлечение структурированных полей;
- локальную регистрацию и session authentication;
- owner-scoped SQLite-архив результатов;
- поиск, фильтрацию, сортировку, pagination, export и удаление;
- строгую валидацию и ограниченные processing limits.

Backend использует feature-oriented структуру вместо плоского каталога `routes/`. Постоянные результаты сохраняются в SQLite, а не в JSON log.

## ✨ Возможности

### Загрузка и предобработка документов

- JPEG, PNG, WebP и PDF;
- drag-and-drop;
- захват с камеры;
- встроенные синтетические demo documents;
- поворот на 90°;
- точный deskew;
- crop;
- grayscale;
- binary threshold;
- invert;
- редактируемый OCR-текст.

### OCR

- Browser OCR через Tesseract.js;
- Server OCR через системный Tesseract и `pytesseract`;
- серверный PDF OCR через `pypdfium2`;
- английский;
- русский;
- английский + русский;
- немецкий;
- французский;
- испанский;
- профили Fast, Standard и Best для Browser OCR;
- metadata OCR: confidence, word count, language, engine и profile.

### AI и результаты

- OpenAI-compatible классификация;
- короткий summary документа;
- confidence, возвращаемый моделью;
- tags;
- структурированные поля label/value;
- фиксированная taxonomy типов документов;
- SQLite-архив результатов;
- поиск по тексту;
- фильтр по classification;
- детерминированная сортировка и pagination;
- detail view;
- JSON export;
- удаление одной записи или всего архива пользователя.

### Безопасность и устойчивость

- Argon2 password hashing;
- opaque server-side sessions;
- HttpOnly session cookie;
- CSRF-защита authenticated mutations;
- персональная ownership-модель сканов;
- cross-user `404` isolation;
- request deadlines и input limits;
- защита от stale responses и смены identity;
- безопасные ошибки provider/storage без утечки secret, local path или traceback.

### Дополнительные возможности

- server-side OCR дополнительно к browser OCR;
- постоянное хранение в SQLite;
- извлечение структурированных полей;
- multilingual OCR;
- PDF OCR;
- user authentication и owner-scoped results;
- проверяемый синтетический sample corpus;
- автоматические backend и frontend quality checks.

## 🔄 Схема работы

```text
                              +----------------------+
                              |  Image / camera      |
                              +----------+-----------+
                                         |
                                         v
                              +----------------------+
                              | Canvas preprocessing |
                              | rotate / crop / etc. |
                              +-----+-----------+----+
                                    |           |
                      Browser OCR   |           |   Server OCR
                                    v           v
                            +-----------+   +-----------+
                            |Tesseract.js|   | FastAPI   |
                            +-----+-----+   | Tesseract |
                                  |         +-----+-----+
                                  +-----+---------+
                                        |
                                        v
                              +----------------------+
                              | Editable OCR text    |
                              +----------+-----------+
                                         |
                                 optional|
                                         v
                              +----------------------+
                              | AI analysis          |
                              | class / summary /    |
                              | tags / fields        |
                              +----------+-----------+
                                         |
                                         v
                              +----------------------+
                              | SQLite scan archive  |
                              +----------------------+

    PDF -> FastAPI -> PDFium render -> Pillow preprocessing -> Tesseract
                                                |
                                                +-> editable text -> AI -> archive
```

Browser-only работа с изображением остаётся доступной даже при недоступном backend. Защищённые server operations требуют authenticated session.

## 🔎 OCR-движки

| Свойство                  | Browser OCR                         | Server OCR                              |
| ------------------------- | ----------------------------------- | --------------------------------------- |
| Engine                    | Tesseract.js `5.1.1`                | системный Tesseract через `pytesseract` |
| Нужен backend             | нет                                 | да                                      |
| Нужен sign-in             | нет                                 | да                                      |
| Input изображения         | текущий обработанный Canvas         | текущий обработанный Canvas PNG         |
| Input PDF                 | не используется                     | исходный PDF                            |
| Предобработка изображения | интерактивные Canvas tools          | уже выполнена в Canvas                  |
| Предобработка PDF         | —                                   | none, grayscale или threshold           |
| Language data             | локальный frontend `traineddata`    | системные Tesseract languages           |
| Preview изображения       | интерактивный Canvas                | тот же Canvas                           |
| Preview PDF               | карточка имени и metadata документа | страницы рендерятся на server для OCR   |

### Профили Browser OCR

| Profile    | Источник Tesseract data       | Назначение                                      |
| ---------- | ----------------------------- | ----------------------------------------------- |
| `fast`     | `tesseract-ocr/tessdata_fast` | самый маленький и быстрый; profile по умолчанию |
| `standard` | `tesseract-ocr/tessdata`      | более крупная general-purpose LSTM model        |
| `best`     | `tesseract-ocr/tessdata_best` | самая крупная model, медленнее startup и OCR    |

В репозитории отслеживаются:

```text
frontend/assets/tessdata/fast/eng.traineddata
frontend/assets/tessdata/fast/rus.traineddata
frontend/assets/tessdata/manifest.json
```

Поэтому сразу после clone доступны Fast English, Russian и English + Russian Browser OCR.

Дополнительные модели:

```bash
npm run ocr:download -- standard eng rus
npm run ocr:download -- best eng rus
```

Проверка локального набора моделей:

```bash
npm run ocr:verify
```

Опциональные Standard и Best models игнорируются Git. Verifier пересоздаёт `manifest.json` по всем локально установленным моделям, поэтому при наличии дополнительных моделей tracked manifest может стать modified в working tree.

Данные OCR-моделей локальные, но pinned Tesseract.js script, worker и WebAssembly core загружаются с jsDelivr.

## 🤖 AI-анализ

AI-анализ опционален и по умолчанию выключен.

Скопируйте пример конфигурации:

```bash
cp backend/.env.example backend/.env
```

Настройте OpenAI-compatible provider:

```dotenv
VISUAL_SCAN_AI_ENABLED=true
VISUAL_SCAN_AI_BASE_URL=https://provider.example/v1
VISUAL_SCAN_AI_API_KEY=replace-with-your-key
VISUAL_SCAN_AI_MODEL=document-model
VISUAL_SCAN_AI_PROVIDER_NAME=openai-compatible
VISUAL_SCAN_AI_RESPONSE_FORMAT=json_object
```

Для локального provider без authentication `VISUAL_SCAN_AI_API_KEY` может быть пустым.

Backend отправляет provider только:

- безопасное имя файла;
- язык OCR;
- распознанный текст.

Исходное изображение или PDF в AI provider не отправляется.

Provider должен вернуть один JSON object:

```json
{
  "classification": "contract",
  "confidence": 0.93,
  "summary": "Short document summary.",
  "tags": ["legal", "employment"],
  "fields": [
    {
      "label": "Effective date",
      "value": "2026-07-30"
    }
  ]
}
```

Поддерживаемые classifications:

```text
invoice
receipt
contract
letter
form
report
statement
identity_document
certificate
business_card
note
other
```

`json_object` отправляет OpenAI-compatible параметр response format. `prompt_only` предназначен для providers, которые его не поддерживают.

AI result никогда не сохраняется автоматически. Пользователь может проверить или отредактировать OCR text, выполнить analysis и затем явно сохранить актуальный результат.

## 🔐 Авторизация и архив

Visual Scan использует opaque server-side sessions вместо JWT.

Registration и login создают HttpOnly, SameSite=Lax session cookie. Raw session token не сохраняется в SQLite — хранится только SHA-256 digest. Passwords хешируются Argon2.

Authenticated unsafe requests дополнительно требуют CSRF token, который хранится только в памяти frontend. Server OCR, PDF OCR, AI analysis, save, archive mutations и legacy-claim являются protected operations.

В archive сохраняются:

```text
filename
scanned_at
полный OCR text
AI classification / confidence / summary / tags / fields
OCR engine / language / profile / confidence / word count
```

Uploaded originals, PDF и thumbnails не сохраняются.

Каждый scan принадлежит одному пользователю. Валидный UUID чужого scan возвращает такой же `404`, как несуществующая запись.

Вкладка **Scanned Results** поддерживает:

- full-text search;
- фильтр classification;
- sorting;
- pagination;
- асинхронную загрузку деталей;
- JSON export;
- удаление одной записи;
- очистку архива текущего пользователя.

Legacy browser-only records хранятся отдельно и могут быть экспортированы или удалены только явным действием. Для старых pre-auth SQLite records предусмотрен отдельный one-time claim flow.

## 📄 Демо-документы

В `public/sample-docs/` лежат шесть синтетических документов.

| Sample                  | Format | Language          | Основное назначение                    |
| ----------------------- | ------ | ----------------- | -------------------------------------- |
| Clean invoice           | PNG    | English           | high-contrast Browser OCR              |
| Compressed receipt      | JPEG   | English           | compression и image adjustments        |
| Skewed Russian contract | PNG    | Russian           | deskew и Russian OCR                   |
| Bilingual letter        | PNG    | English + Russian | combined-language OCR                  |
| Low-contrast note       | PNG    | English           | grayscale и threshold correction       |
| Two-page statement      | PDF    | English           | последовательный multi-page Server OCR |

Все имена, организации, идентификаторы, даты, суммы и операции в corpus вымышлены. Provenance описан в `public/sample-docs/SOURCES.md`.

Fixtures проходят тот же validation и file-loading path, что и пользовательские файлы. Загрузка sample сама по себе не запускает OCR, AI analysis и ничего не сохраняет.

Проверка corpus:

```bash
npm run samples:verify
```

## 🌐 Backend API

FastAPI Swagger UI:

```text
http://localhost:8000/docs
```

### Endpoints

| Method   | Endpoint                  | Auth          | Назначение                             |
| -------- | ------------------------- | ------------- | -------------------------------------- |
| `GET`    | `/api/health`             | нет           | health и configured AI availability    |
| `POST`   | `/api/auth/register`      | нет           | создать локального user и session      |
| `POST`   | `/api/auth/login`         | нет           | authentication и session rotation      |
| `GET`    | `/api/auth/session`       | session check | проверить текущую session              |
| `POST`   | `/api/auth/logout`        | да            | завершить текущую session              |
| `POST`   | `/api/ocr/recognize`      | да            | OCR для JPEG, PNG или WebP             |
| `POST`   | `/api/ocr/pdf/recognize`  | да            | последовательный OCR PDF               |
| `POST`   | `/api/ai/analyze`         | да            | classification и summary OCR text      |
| `POST`   | `/api/scans`              | да            | сохранить один scan                    |
| `GET`    | `/api/scans`              | да            | search, filter, sort и pagination      |
| `GET`    | `/api/scans/{scan_id}`    | да            | получить полные детали scan            |
| `DELETE` | `/api/scans/{scan_id}`    | да            | удалить один owned scan                |
| `DELETE` | `/api/scans`              | да            | очистить archive текущего пользователя |
| `GET`    | `/api/scans/legacy`       | да            | metadata доступного pre-auth archive   |
| `POST`   | `/api/scans/legacy/claim` | да            | один раз забрать legacy records        |

Authenticated mutations требуют session cookie, точный разрешённый `Origin` и `X-CSRF-Token`.

### Health

```http
GET /api/health
```

```json
{
  "status": "ok",
  "ai_available": true,
  "provider": "openai-compatible"
}
```

Health показывает только configured availability и не вызывает внешний AI provider.

### Server image OCR

```http
POST /api/ocr/recognize
Content-Type: multipart/form-data
```

Поля:

```text
file
language=eng|rus|eng+rus|deu|fra|spa
preprocessing=none|grayscale|threshold
threshold=0..255
```

Пример response:

```json
{
  "filename": "invoice.png",
  "text": "Recognized text",
  "confidence": 91.25,
  "words": 2,
  "language": "eng",
  "preprocessing": "none",
  "threshold": null,
  "width": 1240,
  "height": 1754,
  "format": "PNG",
  "engine": "tesseract"
}
```

### PDF OCR

```http
POST /api/ocr/pdf/recognize
Content-Type: multipart/form-data
```

Дополнительное опциональное поле:

```text
password
```

Response содержит объединённый текст документа и page-level text, confidence, word count и rendered dimensions.

### AI analysis

```http
POST /api/ai/analyze
Content-Type: application/json
```

Пример request:

```json
{
  "filename": "contract.jpg",
  "text": "Recognized document text...",
  "language": "eng"
}
```

### Results archive

```http
POST   /api/scans
GET    /api/scans
GET    /api/scans/{scan_id}
DELETE /api/scans/{scan_id}
DELETE /api/scans
```

List parameters:

```text
q
classification
sort=scanned_at|filename|classification|confidence
order=asc|desc
limit=1..200
offset
```

SQLite по умолчанию находится здесь:

```text
backend/data/visual-scan.db
```

Каталог базы создаётся автоматически. SQLite работает с WAL и `synchronous=FULL`.

## 🧰 Стек технологий

| Слой           | Технология                                      |
| -------------- | ----------------------------------------------- |
| Frontend       | HTML, CSS, Vanilla JavaScript ES modules        |
| Image tools    | Canvas API                                      |
| Browser OCR    | Tesseract.js `5.1.1`                            |
| Backend        | Python `3.11+`, FastAPI, Uvicorn                |
| Server OCR     | Tesseract, `pytesseract`, Pillow                |
| PDF rendering  | `pypdfium2`                                     |
| AI transport   | `httpx` + OpenAI-compatible `/chat/completions` |
| Validation     | Pydantic / `pydantic-settings`                  |
| Storage        | SQLite + WAL                                    |
| Authentication | Argon2, opaque HttpOnly sessions, CSRF          |
| Backend tests  | pytest, Ruff                                    |
| Frontend tests | встроенный test runner Node.js                  |
| CI             | GitHub Actions                                  |

У frontend нет build step и нет npm runtime dependencies, которые нужно устанавливать перед запуском.

## 🧪 Тесты и проверки

### Development installation

```bash
python -m pip install -e "./backend[dev]"
```

`requirements.txt` содержит runtime dependencies. `backend/pyproject.toml` содержит package metadata backend, те же runtime constraints и development extras. Regression test не даёт двум спискам зависимостей разъехаться.

### Backend

```bash
python -m pytest backend/tests
python -m ruff check backend
python -m ruff format --check backend
python -m compileall backend/app backend/tests
python backend/scripts/generate_dependency_graph.py --check
```

### Frontend и fixtures

```bash
npm run ocr:verify
npm run samples:verify
npm test
```

Backend suite покрывает API contracts, validation, OCR pipelines, PDF rendering, SQLite schema и concurrency, authentication, ownership, AI response parsing и dependency boundaries.

Protocol tests AI provider используют `httpx.MockTransport`. Они проверяют request payload, Authorization, response parsing, timeouts, status mapping и malformed responses без вызова живого внешнего endpoint и без API key.

Frontend tests покрывают API transport, archive behavior, authentication state transitions, cross-tab identity synchronization, camera races, OCR model availability, samples и legacy storage behavior.

GitHub Actions запускает backend tests на Python `3.11` и `3.14`, backend quality checks на Python `3.11`, а frontend/model/sample checks — на Node.js `24`.

## 📁 Структура проекта

```text
visual-scan/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── router.py
│   │   ├── core/
│   │   │   └── config.py
│   │   ├── features/
│   │   │   ├── analysis/
│   │   │   ├── auth/
│   │   │   ├── health/
│   │   │   ├── ocr/
│   │   │   └── scans/
│   │   ├── storage/
│   │   ├── factory.py
│   │   └── main.py
│   ├── data/
│   ├── tests/
│   ├── .env.example
│   ├── ARCHITECTURE.md
│   ├── DEPENDENCY_GRAPH.md
│   ├── module-map.json
│   └── pyproject.toml
├── frontend/
│   ├── assets/
│   │   └── tessdata/
│   │       ├── fast/
│   │       ├── standard/
│   │       ├── best/
│   │       └── manifest.json
│   ├── utils/
│   ├── app.js
│   ├── config.js
│   ├── index.html
│   ├── intakeContract.js
│   ├── module-map.json
│   ├── ocrProfiles.js
│   └── styles.css
├── public/
│   └── sample-docs/
├── scripts/
│   ├── download-ocr-models.mjs
│   ├── verify-ocr-models.mjs
│   └── verify-sample-docs.mjs
├── tests/
├── .github/
│   └── workflows/
│       └── ci.yml
├── AGENTS.md
├── package.json
├── requirements.txt
├── README.md
└── README_RU.md
```

Backend responsibilities feature-local: каждая feature владеет своим router, schemas, service layer, implementation details и errors. `backend/app/api/router.py` только собирает публичные routers.

Frontend state и UI orchestration находятся в `frontend/app.js`; reusable transport, auth, OCR, image, sample, camera и archive logic вынесены в `frontend/utils/`.

`backend/module-map.json` и `frontend/module-map.json` фиксируют ownership и dependency boundaries. `backend/DEPENDENCY_GRAPH.md` генерируется из backend map и проверяется на drift.

## ⚠️ Примечания

- Browser OCR поставляется с Fast English и Russian model data, но Tesseract.js runtime и WebAssembly core загружаются по pinned jsDelivr URLs.
- Standard и Best browser models опциональны и не коммитятся.
- Server OCR требует нативный Tesseract executable и установленные language packs.
- Для PDF frontend показывает карточку с именем и metadata вместо client-side page preview; для OCR страницы рендерятся на server.
- Server OCR, PDF OCR, AI analysis, save и server results archive требуют sign-in.
- AI выключен, пока provider не настроен в `backend/.env`.
- AI provider получает OCR text и metadata, но не исходное изображение документа.
- Uploaded originals и thumbnails приложение не сохраняет.
- Новые результаты сохраняются в owner-scoped SQLite archive, а не в browser `localStorage`.
- `backend/.env`, SQLite database и опциональные локальные OCR models игнорируются Git.
- Документированная локальная browser topology использует `localhost` для frontend и backend; не смешивайте `localhost` и `127.0.0.1` в одной authenticated browser session.

## 🧑‍💻 Автор
Nazar Yestayev (@nyestaye)
