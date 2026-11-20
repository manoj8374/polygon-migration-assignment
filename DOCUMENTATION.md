# Polygon Migration Tool — Functionality Documentation

This document explains how the Polygon Migration Tool actually works, so a new engineer can follow any user action from the browser, through the Django backend, to the database, Redis, Polygon, or cloud storage. It describes the **current implementation** (including its quirks), not an idealised version. File and function names are given throughout so you can jump straight to the code.

All paths below are relative to `PolygonMigration/` (the folder containing `manage.py`) unless stated otherwise.

---

## 0. Architecture at a glance

| Piece | Where | Role |
|---|---|---|
| Django project / settings | `PolygonMigration/settings.py`, `PolygonMigration/urls.py` | Loads `.env`, configures PostgreSQL, Redis, Polygon and storage settings, URL routing |
| `users` app | `users/` | Custom `User` model (email login), `EmailBackend`, login/logout views, login template |
| `problems` app | `problems/` | The whole migration tool: one view (`views.index`), the Polygon client (`polygon_api.py`), models, storage layer, one template |
| `problems/storage/` | `base.py`, `s3.py`, `azure.py`, `__init__.py` | Provider-independent storage interface (`TestCaseStorage`) plus S3 and Azure implementations; `get_storage()` is the factory |
| `contents` app | `contents/` | Only a `Topic` model (referenced by `User.on_going_topic`); not part of the migration flow |
| PostgreSQL | configured in `settings.DATABASES` | Stores users, sessions, problems, tags, sample/regular test cases |
| Redis | `settings.REDIS_*` | 30-minute cache of test cases fetched from Polygon |
| Polygon API | `https://polygon.codeforces.com/api/` | Source of problem data |
| Cloud storage (S3 or Azure Blob) | chosen by `STORAGE_PROVIDER` | Holds test-case files as objects |

### URL map

| URL | View | Notes |
|---|---|---|
| `/` | `problems.views.index` | The single page of the tool; GET renders it, POST performs every action |
| `/users/login/` | `users.views.login_view` | Staff login |
| `/users/logout/` | `users.views.logout_view` | Logs out and redirects to the login page |
| `/admin/` | Django admin | Inspect `Problem`, `ProblemTag`, `SampleTestCase`, `ProblemTestCase`, `User`, `Topic` |

### The one-view design (read this first)

Every action on the main page is a **POST to `/`**, handled by `index()` in `problems/views.py`. The action is selected by which hidden field the submitted form carries:

| Form (id in `index.html`) | Button label in the UI | Hidden/trigger field | What `index()` does |
|---|---|---|---|
| `fetchForm` | **Fetch Problem** | only `problem_id` | Fetch + display only |
| `migrate-db-form` | **Create/Update problem in Database** | `migrate_to_db=1` (+ `difficulty`, `tags`) | Fetch + save problem, tags, **sample** test cases |
| `migrate-test-cases-form` | **Migrate Test Description to Database** | `migrate_test_cases_to_db=1` | Fetch + save **all** test cases into `ProblemTestCase` |
| `migrate-azure-form` | **Migrate Test Cases to Storage** | `migrate_to_azure=1` | Fetch + upload files to cloud storage |

> **Naming note.** The assignment and the README call these “Migrate to Database”, “Migrate Test Cases to DB” and “Upload to Azure/Storage”. The labels in the shipped template are the ones in the table above. The POST field `migrate_to_azure` kept its historical name even though it now means “migrate to whichever storage provider is configured”.

Because it is one view, **every click re-runs the same “always fetch for display” block** (Polygon info, package download, checker, test cases, …) before doing the action-specific work. That is why a single click can take a long time and why the same Polygon endpoints appear in several flows.

### Models (`problems/models.py`)

- **`Problem`** — `polygon_id` (unique), `title`, `slug` (unique), `difficulty` (`easy|medium|hard`), `problem_statement`, `input_format`, `output_format`, `constraints`, `editorial`, `time_limit` (ms), `memory_limit` (MB), `checker_type`, `test_case_count`, `notes`, plus several flags (`is_locked`, `genie_*`, …) that this tool never sets. `extra_tags` is a many-to-many to `ProblemTag`.
- **`ProblemTag`** — `tag_name` (unique).
- **`SampleTestCase`** — FK to `Problem`, `input`, `output`, `order`. Used for the examples shown in a statement.
- **`ProblemTestCase`** — FK to `Problem`, `is_sample`, `input`, `output`, `description`, `order`, timestamps. Holds *all* tests (samples included), but with truncated content (see Flow 4).
- **`users.User`** — custom user (`USERNAME_FIELD = 'email'`), with `is_staff` controlling access to the tool.

The **database primary key** of `Problem` (`Problem.id`) — not the Polygon ID — is what names the files in cloud storage.

---

## 1. Local setup (summary)

1. Python venv, then `pip install -r ../requirement.txt` (the file is at the repo root and is called `requirement.txt`, singular).
2. Start PostgreSQL and Redis; copy `.env.example` to `.env` next to `manage.py` and fill it in (see §3 for the variables).
3. `python manage.py migrate`, `python manage.py createsuperuser` (prompts for email, first/last name, username, contact number, college, graduation year, gender — these are `REQUIRED_FIELDS` of the custom user), `python manage.py runserver`.
4. Open `http://localhost:8000/` → you are redirected to `/users/login/`.

Things that will surprise you:

- `settings.py` **raises at import time** if `POLYGON_API_KEY` / `POLYGON_API_SECRET` are missing, so even `migrate` and `createsuperuser` fail without them.
- `createsuperuser` sets `is_staff=True`, which is what the login view requires.
- Redis is described as “optional”, but without it every step re-downloads all tests from Polygon (see §2.7).
- Polygon only returns a package if you have **built a package** for the problem on Polygon (Packages → Build). Otherwise the fetch fails with “No packages available for this problem”.
- A custom checker is compiled with `g++`; without `g++` the checker source is uploaded instead.

---

## 2. User flows

### Flow 1 — User login

**What the user does.** Opens `/` (or `/users/login/`), types email and password on the “Staff Login” card, clicks **Login**.

**What the frontend sends.** `users/templates/users/login.html` posts a normal HTML form to `/users/login/` with `email`, `password` and Django's `csrfmiddlewaretoken`. No JavaScript is involved.

**What the backend does, in order** (`users.views.login_view`):

1. If the request is from an already-authenticated user, call `logout(request)` first (so visiting the login page always logs you out).
2. On GET: render `users/login.html`.
3. On POST: read `email` and `password` and call `authenticate(request, username=email, email=email, password=password)`.
4. Django walks `AUTHENTICATION_BACKENDS` (`settings.py`): first `users.backends.EmailBackend`, then the default `ModelBackend`. `EmailBackend.authenticate` looks up `User.objects.get(email=email)`, then checks `user.check_password(password)` and `user_can_authenticate(user)` (i.e. `is_active`). It returns the user or `None`.
5. If a user came back **and** `user.is_staff` is true → `login(request, user)` (creates a session) and `redirect('problems:index')`.
6. If a user came back but is not staff → `messages.error('You do not have staff access.')` and the login page is re-rendered.
7. If `authenticate` returned `None` → `messages.error('Invalid email or password.')`, page re-rendered.

**External APIs.** None.

**Database.** Reads `users_user` by email; on success, writes a row to `django_session` (session engine is the Django default, database-backed). Session lifetime is one day (`SESSION_COOKIE_AGE = 86400`).

**What the user sees.** Success: the main “Hatsu: Problem Migrator” page with “Welcome, First Last” and a Logout button in the navbar. Failure: a red alert on the login card.

**How access is enforced on the main page.** `problems.views.index` is decorated with `@user_passes_test(lambda u: u.is_authenticated and u.is_staff, login_url='/users/login/')`. An anonymous or non-staff visitor is redirected to `/users/login/?next=/`. (`login_view` ignores the `next` parameter; after login you always land on `problems:index`.)

**Logout.** The navbar button is a POST form to `/users/logout/`; `logout_view` calls `logout(request)` and redirects to the URL named `login`.

---

### Flow 2 — Fetching a problem from Polygon

**What the user does.** Types the Polygon problem ID (the numeric ID shown in the Polygon URL) into the box and clicks **Fetch Problem**.

**What the frontend sends.** `fetchForm` is intercepted by JavaScript (`e.preventDefault()`), which shows the loading overlay (“Fetching problem data and test cases (may take 3-4 minutes)…”) and then submits the form after 50 ms. The POST to `/` contains only `problem_id` and the CSRF token.

**What the backend does, in order** (`problems.views.index`, `migrate_*` flags all empty):

1. Build the list of existing tags for the tag picker (`all_tags`, `all_tags_json`).
2. Create a `PolygonAPI()` client (reads `POLYGON_API_KEY` / `POLYGON_API_SECRET` from settings).
3. Look up `Problem.objects.filter(polygon_id=polygon_id).first()`. **If the problem was already migrated**, also:
   - call `problem.updateWorkingCopy` (refreshes your Polygon working copy),
   - call `problem.solutions`, pick the solution tagged `MA` (main) or else the first, and call `problem.viewSolution` for its source → shown as “Main Correct Solution”.
   - set the difficulty dropdown and tags to the stored values.
   If the problem is *not* in the DB yet, the solution section shows “Please migrate the problem to the database first.” (the solution is only fetched for problems already in the DB, or right after a DB migration).
4. Inside `transaction.atomic()`:
   1. `api.get_problem_info()` → `problem.info` (time limit, memory limit, name).
   2. `api.download_and_extract_package()` → `problem.info` again, `problem.packages` (finds the newest package of type `standard`), `problem.package` (downloads the ZIP binary). The ZIP is extracted into a temp dir, `problem.html` is read, and the temp dir is deleted.
   3. `parse_problem_html()` (lxml/XPath) extracts the `title`, the `legend` (statement), `input-specification`, `output-specification` and `note` blocks.
   4. `api.get_all_test_cases()` → `problem.tests` (list of tests with `index`, `useInStatements`, `description`, …) and then, **for every test**, `problem.testInput` and `problem.testAnswer` (two extra calls per test). If one of those calls fails, that test is kept with empty input/output rather than aborting.
   5. The result is cached with `api.store_test_cases_in_redis(..., expiry_hours=0.5)`.
   6. `problem.checker` is called to learn the checker; `std::` and `.cpp` are stripped, and any name not in the standard list becomes `custom`. `get_custom_checker_info()` calls `problem.checker` once more to decide whether to display a “Custom Checker”.
   7. `api.get_test_cases()` → `problem.tests` once more, only to count tests (`test_case_count`).
   8. A `fetched_problem` dict is built (`title`, `slug = slugify(title)`, statement, formats, notes, limits, checker type, test count). `constraints` and `editorial` are hard-coded to empty strings — nothing is parsed for them.
5. Test-case rows are prepared for display (50-character previews plus full text for the “View Full” modal).
6. Render `problems/index.html`.

**External APIs.** `problem.info` (×2), `problem.packages`, `problem.package`, `problem.tests` (×2), `problem.testInput` (×N), `problem.testAnswer` (×N), `problem.checker` (×2); additionally `problem.updateWorkingCopy`, `problem.solutions`, `problem.viewSolution` if the problem already exists in the DB. Authentication is described in §3.1.

**Database / storage.** Reads `Problem` (and its tags if it exists). **Nothing is written to the database or cloud storage.** Redis receives `polygon_migration_test_cases_{polygon_id}_count` and `..._test_{n}` keys with a 30-minute TTL.

**What the user sees.** A “Fetched Problem: *title* (Polygon ID: …)” section containing: Database Problem ID and Algopath link (or “Please migrate to database first.”), slug, difficulty selector, tag picker, time/memory limits, checker type (and custom checker name if any), test count; the statement, input format, output format and notes; the three action buttons; a table of all test cases (index, input/output previews, description, “Sample Test Case” yes/no); and the main solution block. Statement/format/notes HTML is shown through `|linebreaksbr`, which auto-escapes, so tags such as `<p>` and `<i>` appear as literal text rather than rendered formatting. Errors appear in a red “Error” alert.

---

### Flow 3 — Migrating a problem to the database

**What the user does.** After fetching, chooses a **difficulty** (required), picks or creates **tags**, and clicks **Create/Update problem in Database**.

**What the frontend sends.** `migrate-db-form` posts: `problem_id`, `migrate_to_db=1`, `difficulty` (a hidden input kept in sync with the dropdown by JS), and one hidden `tags` input per selected tag (rebuilt by `updateHiddenInputs()` on submit). New tags typed in the search box are offered as “Add "xyz"” and are sent as ordinary `tags` values; the server's separate `new_tag` field exists but this template never sends it. If no difficulty is chosen, JavaScript blocks submit with an alert; the server re-checks.

**What the backend does, in order** (`index`, `migrate_to_db` set):

1. Runs the full “always fetch” block from Flow 2 (all the Polygon calls). Because `migrate_to_db` is set, test cases are first looked up in Redis and only fetched from Polygon if the cache is empty.
2. Validate difficulty — if empty, raise (the whole transaction rolls back and “Migration failed and all changes have been rolled back. Reason: …” is shown).
3. `Problem.objects.filter(polygon_id=polygon_id).first()`:
   - **exists** → overwrite `title`, `slug`, `difficulty`, `problem_statement`, `input_format`, `output_format`, `constraints`, `editorial`, `time_limit`, `memory_limit`, `checker_type`, `test_case_count`, `notes` and `save()`; message “Problem '…' updated in database successfully.”
   - **does not exist** → `Problem.objects.create(...)` with the same fields; message “… created in database successfully.”
   (If the custom checker was detected, “Custom checker '…' detected.” is appended.)
4. **Tags:** `problem_obj.extra_tags.clear()`, then for each submitted tag `ProblemTag.objects.get_or_create(tag_name=...)` and `add()`; the `new_tag` value is handled the same way. Net effect: the tags submitted *replace* whatever was there.
5. **Sample test cases:** take the cached/fetched tests where `is_sample` (Polygon's `useInStatements`) is true; for each, `rstrip()` input and output and skip it if either is empty; update the existing `SampleTestCase` at the same position or create a new one, with `order` 1, 2, 3, … Existing sample rows beyond the new count are **not** deleted.
6. Reload the `Problem`, then fetch the main solution (`problem.solutions` + `problem.viewSolution`) for display.
7. Commit the transaction and render the page.

**External APIs.** Same as Flow 2, plus `problem.solutions` and `problem.viewSolution`.

**Database.** Writes: `problems_problem` (insert or update), `problems_problemtag` (insert for new names), the `extra_tags` join table (cleared then refilled), `problems_sampletestcase` (insert/update). All inside one `transaction.atomic()` block. Constraints that can make this fail and roll back: `Problem.polygon_id` unique and `Problem.slug` unique (two different problems with the same title produce the same slug).

**What the user sees.** A blue “Database Operation” alert (“Problem '…' created/updated in database successfully.”), the Database Problem ID and Algopath link filled in, the tags shown as chips, the main solution displayed, and the two migration buttons (storage and test cases) now enabled because the problem exists in the DB.

---

### Flow 4 — Migrating test cases to the database

**What the user does.** Clicks **Migrate Test Description to Database** (shown below the test-case table; disabled until the problem exists in the DB).

**What the frontend sends.** `migrate-test-cases-form` posts `problem_id` and `migrate_test_cases_to_db=1`. (Note: it does not send difficulty or tags.)

**What the backend does, in order** (`index`, `migrate_test_cases_to_db` set):

1. Runs the full “always fetch” block again (Redis is used for the test cases if present).
2. Requires that `Problem` with this `polygon_id` exists; otherwise raises “Please migrate the problem to the database first.” (rolled back, shown as an error).
3. Gets the test cases from Redis, or from Polygon (and re-caches them).
4. Loads existing `ProblemTestCase` rows for the problem ordered by `order`.
5. For each test, by position `idx`:
   - `input` and `output` are `rstrip()`-ed;
   - tests where either is empty are **skipped** (and consume no `order` number);
   - input and output are **truncated to their first 260 characters** (`[:260]`);
   - if a row exists at that position (`idx < len(existing)`), it is overwritten (`input`, `output`, `is_sample`, `description`, `order`); otherwise a new row is created with `order` = 1, 2, 3, …
6. Sets the success message “Test cases description migrated to database Successfully.”

Rows left over from an earlier, larger migration are not removed.

**External APIs.** Same “always fetch” calls as Flow 2 (none specific to this flow).

**Database.** Writes `problems_problemtestcase` (insert/update only). Does **not** change `Problem.test_case_count`, and does not touch `SampleTestCase`.

**What the user sees.** A green “Success” alert. The test-case table still shows the full (untruncated) Polygon data from the fetch, not what was stored; check the stored rows in `/admin/problems/problemtestcase/`.

> **Why the truncation matters.** The DB copy is only a *preview/description* of each test (the full files go to cloud storage in Flow 5). The 260-character cut-off is silent: there is no marker on the stored value and no UI warning.

---

### Flow 5 — Uploading test cases to cloud storage

**What the user does.** Clicks **Migrate Test Cases to Storage** (disabled until the problem exists in the DB).

**What the frontend sends.** `migrate-azure-form` posts `problem_id` and `migrate_to_azure=1`; the overlay shows “Migrating to storage…”.

**What the backend does, in order** (`index`, `migrate_to_azure` set):

1. Look up the `Problem` by `polygon_id`. If missing, return the page with an error (“…has not been migrated to the database yet…”).
2. `storage = get_storage()` (`problems/storage/__init__.py`): reads `settings.STORAGE_PROVIDER` (`s3` or `azure`, default `azure`) and builds `S3Storage` (boto3) or `AzureStorage` (`UsernamePasswordCredential` → `BlobServiceClient`). An unknown value raises `ImproperlyConfigured`.
3. `problem_id = problem_obj.id` — the **database** ID is used for naming.
4. `api.delete_problem_test_case_cache(problem_id)` is called. It deletes Redis keys matching `oj_dev_with_redis_storage_test_cases_{db_id}*`, which is a *different* prefix from the one this tool writes (`polygon_migration_test_cases_{polygon_id}`), so in practice it removes nothing and the 30-minute test-case cache stays in use.
5. `api.get_custom_checker_info()` (`problem.checker`) — if the checker is not a `std::` one, an info message is prepared.
6. `api.migrate_to_storage(polygon_id, storage, problem_id)`:
   1. Get test cases from Redis, or `get_all_test_cases()` from Polygon.
   2. `storage.delete_problem_files(db_id)` — **deletes everything** under `test_cases/{db_id}/` first.
   3. For every test with `enumerate(..., start=1)`: if both input and output are non-empty, `storage.upload_test_case(db_id, idx, input, output)` (two objects); otherwise log a warning and skip it. The test number `idx` is the position in Polygon's list (samples included, in Polygon order), so a skipped test leaves a gap in the numbering.
   4. `upload_custom_checker()`: for a non-standard checker, fetch its source with `problem.viewFile` (tries type `source`, then `resource`, then with `.cpp` appended), compile it with `g++ -std=gnu++17 -O2`, and upload the binary (`custom_checker` or `custom_checker.exe` on Windows) via `storage.upload_file`; if compilation fails the source `custom_checker.cpp` is uploaded instead.
7. Then the usual “always fetch” block runs (Flow 2) to redisplay the problem.
8. Message: “Test cases migrated to storage (s3) successfully.” (plus the checker sentence if relevant).

**Failure handling.** The whole view runs inside `transaction.atomic()`. If anything raises, the DB changes roll back, Redis keys for this problem are cleared (`clear_test_cases_from_redis`), and — if the upload had *completed* — the uploaded files are deleted (`storage.delete_problem_files`) as a compensation step. The user sees “Migration failed and all changes have been rolled back. Reason: …”. Storage is not part of the DB transaction, so a failure *during* upload leaves whatever had been uploaded so far in place.

**External APIs.** Polygon: as in Flow 2, plus `problem.viewFile` for custom checkers. Cloud: S3 `ListObjectsV2`/`DeleteObjects`/`PutObject`, or Azure Blob list/delete/upload.

**Database / storage.** Database: read-only (`Problem` lookup). Storage: delete-then-write under `test_cases/{db_id}/`. Redis: cache read/write as above.

**What the user sees.** A green “Success” alert. To verify, open your bucket/container and look for the layout in §3.2.

#### Database vs. storage — the difference

| | Database (Flows 3 & 4) | Cloud storage (Flow 5) |
|---|---|---|
| Holds | Problem metadata, statement, tags, sample tests, *truncated* copies of tests | The *full* input/output of every test as files (and the checker) |
| Purpose | Query/list/search problems and show statements | Cheap, scalable bulk storage for the judge to read tests from |
| Size limit | 260 chars per test field (regular tests) | None imposed by this code |
| Keyed by | `Problem.id`, `polygon_id` | `test_cases/{Problem.id}/…` |
| Idempotent re-run | Updates rows in place (never deletes) | Deletes the whole folder and re-uploads |

---

### 2.7 Redis caching (cross-cutting)

`PolygonAPI.store_test_cases_in_redis / get_test_cases_from_redis / clear_test_cases_from_redis` implement a 30-minute cache of the (potentially hundreds of) per-test API calls:

- keys: `polygon_migration_test_cases_{polygon_id}_count` and `polygon_migration_test_cases_{polygon_id}_test_{n}`;
- a plain **Fetch** always bypasses the cache and refreshes it; the three migrate actions read from it when present;
- every Redis helper catches all exceptions and logs them, so a missing/unreachable Redis does not crash the app — it just means each action re-downloads all tests from Polygon;
- because of this, test data edited on Polygon within the last 30 minutes can be migrated stale unless you press **Fetch Problem** again first.

---

## 3. External integrations

### 3.1 Polygon API (`problems/polygon_api.py`, class `PolygonAPI`)

**Base URL:** `https://polygon.codeforces.com/api/<method>`. Every call is an HTTP **POST** with form-encoded parameters (`requests.post(url, data=...)`).

**Authentication.** Polygon uses a signed request scheme. For each call, `_generate_api_sig()`:

1. adds `apiKey` (from `POLYGON_API_KEY`) and `time` (current Unix timestamp) to the parameters;
2. sorts all parameters lexicographically and URL-encodes them (`a=1&b=2…`);
3. generates a random 6-character string `rand`;
4. hashes the string `rand/<method>?<sorted params>#<POLYGON_API_SECRET>` with **SHA-512**;
5. sets `apiSig = rand + hex_digest`.

The POST body then carries the original parameters plus `apiKey`, `time` and `apiSig`. The secret itself is never sent.

**Response handling.** JSON endpoints return `{"status": "OK"|"FAILED", "result": ..., "comment": ...}`; `_make_request` raises `Polygon API Error: <comment>` on `FAILED` and otherwise returns `result`. Plain-text endpoints (`testInput`, `testAnswer`, `viewSolution`, `viewFile`) are read with `expect_json=False` and returned as raw text — so an error body returned as text would be treated as content. `problem.package` returns a ZIP; the code checks the first bytes are `PK` before extracting.

**Endpoints used**

| Endpoint | Used by | Purpose |
|---|---|---|
| `problem.info` | `get_problem_info` | Time limit, memory limit, name |
| `problem.packages` | `download_and_extract_package` | List built packages; newest `standard` package is chosen |
| `problem.package` | `download_and_extract_package` | Download the package ZIP to read `problem.html` |
| `problem.tests` | `get_all_test_cases`, `get_test_cases` | List tests (`index`, `useInStatements`, `description`, `manual`) |
| `problem.testInput` | `get_all_test_cases` | Input text of one test |
| `problem.testAnswer` | `get_all_test_cases` | Expected output of one test |
| `problem.checker` | `index`, `get_custom_checker_info` | Name of the checker (`std::ncmp.cpp`, or a custom file) |
| `problem.solutions` | `index` | List solutions (tag `MA` = main) |
| `problem.viewSolution` | `index` | Source of a solution |
| `problem.updateWorkingCopy` | `index` (only for already-migrated problems) | Refresh the working copy before reading the solutions |
| `problem.viewFile` | `fetch_custom_checker_file` | Source of a custom checker |

`PolygonAPI` also has `get_statements`, `get_test_script`, `get_problem_files` and `get_file_content`, which are not called by the current flows.

### 3.2 Cloud storage (`problems/storage/`)

**Interface.** `TestCaseStorage` (`base.py`) defines the contract the migration code depends on — `upload_test_case`, `upload_file`, `delete_problem_files` — and the shared naming helpers. `polygon_api.migrate_to_storage` and `upload_custom_checker` only ever talk to this interface; `views.index` only calls `get_storage()`. Adding a provider means subclassing `TestCaseStorage`, and adding a branch in `get_storage()`; the migration flow does not change.

**Selecting a provider.** `STORAGE_PROVIDER=s3` or `azure` in `.env` (default `azure` if unset).

| Provider | Class | Config (all from environment variables via `settings.py`) |
|---|---|---|
| AWS S3 / S3-compatible | `S3Storage` (`s3.py`, boto3) | `AWS_S3_BUCKET_NAME` (required), `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, optional `AWS_S3_ENDPOINT_URL` (R2, MinIO). If keys are blank, boto3's default credential chain applies. |
| Azure Blob | `AzureStorage` (`azure.py`) wrapping `AzureBlobManager` (`AzureTestcase.py`) | `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_USERNAME`, `AZURE_PASSWORD`, `AZURE_CONTAINER_NAME` (username/password auth; will not work with MFA) |

**Naming and layout** (identical for every provider, defined once in `TestCaseStorage`):

```
test_cases/{problem_id}/{NN}           # input of test NN
test_cases/{problem_id}/{NN}.a         # expected output of test NN
test_cases/{problem_id}/custom_checker       # compiled custom checker (Linux/macOS), if any
test_cases/{problem_id}/custom_checker.exe   # compiled custom checker (Windows), if any
test_cases/{problem_id}/custom_checker.cpp   # fallback if compilation failed
```

- `{problem_id}` is the **database** `Problem.id`, not the Polygon ID.
- `{NN}` is the 1-based position of the test in Polygon's `problem.tests` list, zero-padded to **two digits** (`01`, `02`, … `10`, … `100`); the output has the `.a` suffix. Sample tests are included, in Polygon order.
- Text is stored UTF-8 encoded.
- Every upload to a problem first deletes the entire `test_cases/{problem_id}/` prefix, so the folder always mirrors the latest migration.
- Behavioural difference to know about: the S3 implementation lets errors propagate (the migration fails visibly); the Azure wrapper (`AzureBlobManager.upload_test_case`) logs upload errors and swallows them.

**How to verify an upload.** List the prefix `test_cases/<Problem.id>/` in your bucket/container (find `<Problem.id>` in the “Database Problem ID” row on the page), expect `2 × tests` objects (fewer if some tests had empty input/output), and open e.g. `01` and `01.a` to confirm input and expected output.

### 3.3 PostgreSQL and Redis

- **PostgreSQL** (`settings.DATABASES`, `psycopg2`): all persistent data — users, sessions, problems, tags, tests. SQLite is not used.
- **Redis** (`REDIS_HOST/PORT/PASSWORD/SSL`): test-case cache only (§2.7). Not used for sessions or Django's cache framework.

---

## 4. Environment variables

| Variable | Used for |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Django |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_SSL` | Redis cache |
| `POLYGON_API_KEY`, `POLYGON_API_SECRET` | Polygon authentication (required at startup) |
| `STORAGE_PROVIDER` | `s3` or `azure` |
| `AWS_S3_BUCKET_NAME`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL` | S3 provider |
| `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_USERNAME`, `AZURE_PASSWORD`, `AZURE_CONTAINER_NAME` | Azure provider |
| `CUSTOM_CHECKER_DIR` | Optional working directory for compiling custom checkers (a temp dir is used if unset) |

`.env` is git-ignored; `.env.example` documents every variable.

---

## 5. Quick code map

| I want to… | Look at |
|---|---|
| Change what happens on a button click | `problems/views.py` → `index()` (branches on `migrate_to_db`, `migrate_test_cases_to_db`, `migrate_to_azure`) |
| Change how the statement is parsed | `problems/views.py` → `parse_problem_html()` |
| Add/modify a Polygon call or signing | `problems/polygon_api.py` → `_generate_api_sig`, `_make_request` |
| Change the storage layout | `problems/storage/base.py` → `input_key`, `output_key`, `file_key` |
| Add a storage provider | new class in `problems/storage/`, branch in `get_storage()` |
| Change login rules | `users/views.py` → `login_view`; `users/backends.py` |
| Change the UI | `problems/templates/problems/index.html` (form ids, JS for tags/loading overlay) |
| Inspect data | Django admin at `/admin/` |
