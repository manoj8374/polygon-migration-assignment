# Polygon Migration Tool — Documentation

How the tool works, from browser to backend to database / Polygon / cloud storage. It describes the **current implementation**, quirks included. Paths are relative to `PolygonMigration/` (the folder with `manage.py`).

## 1. Overview

| Piece | Where | Role |
|---|---|---|
| Settings / URLs | `PolygonMigration/settings.py`, `urls.py` | Loads `.env`; configures Postgres, Redis, Polygon, storage |
| `users` app | `users/` | Custom email-login `User`, `EmailBackend`, login/logout views |
| `problems` app | `problems/` | The tool itself: `views.index`, `polygon_api.py`, models, `storage/`, one template |
| `contents` app | `contents/` | Only a `Topic` model; unrelated to migration |
| PostgreSQL | `settings.DATABASES` | Users, sessions, problems, tags, test cases |
| Redis | `settings.REDIS_*` | 30-minute cache of test cases fetched from Polygon |
| Cloud storage | `STORAGE_PROVIDER` (`s3` / `azure`) | Test-case files |

**URLs:** `/` → `problems.views.index` · `/users/login/` → `login_view` · `/users/logout/` → `logout_view` · `/admin/`.

**One view does everything.** Every button on `/` POSTs to `index()` (`problems/views.py`); the hidden field decides the action:

| Form | UI button | Field | Action |
|---|---|---|---|
| `fetchForm` | Fetch Problem | `problem_id` only | Fetch and display |
| `migrate-db-form` | Create/Update problem in Database | `migrate_to_db` | Save problem, tags, sample tests |
| `migrate-test-cases-form` | Migrate Test Description to Database | `migrate_test_cases_to_db` | Save all tests to `ProblemTestCase` |
| `migrate-azure-form` | Migrate Test Cases to Storage | `migrate_to_azure` | Upload files to storage |

(The assignment calls these “Migrate to Database / Migrate Test Cases to DB / upload to storage”; the table shows the real labels. `migrate_to_azure` is a legacy name meaning “the configured provider”.)

Every click first re-runs the same **fetch-for-display block** (Polygon info, package, checker, tests), then the action-specific work. That is why every flow shares the same Polygon calls and is slow.

**Models** (`problems/models.py`): `Problem` (unique `polygon_id` and `slug`, difficulty, statement fields, limits, `checker_type`, `test_case_count`, `notes`, tags M2M), `ProblemTag`, `SampleTestCase`, `ProblemTestCase` (`is_sample`, `input`, `output`, `description`, `order`).

**Setup gotchas:** `settings.py` raises if `POLYGON_API_KEY/SECRET` are missing (even for `migrate`); `createsuperuser` sets `is_staff`, which login requires; the problem needs a **built package** on Polygon or fetch fails; custom checkers need `g++` (else the `.cpp` source is uploaded); without Redis everything still works but re-downloads all tests each click.

## 2. User flows

### Flow 1 — Login
- **User does:** enters email and password at `/users/login/`, clicks Login.
- **Sent:** plain form POST: `email`, `password`, CSRF token (`login.html`, no JS).
- **Backend (`users.views.login_view`):**
  1. Logs out any already-authenticated user.
  2. Calls `authenticate(request, username=email, email=email, password=...)`.
  3. `users.backends.EmailBackend` gets the user by email, checks the password and `is_active`.
  4. If the user is `is_staff`: `login()` and redirect to `problems:index`. Otherwise an error message (“You do not have staff access.” / “Invalid email or password.”).
- **External APIs:** none.
- **DB:** reads `users_user`; writes a `django_session` row (1-day lifetime).
- **User sees:** the “Hatsu: Problem Migrator” page with a welcome line and Logout, or a red alert.
- **Access control:** `index` uses `@user_passes_test(is_authenticated and is_staff, login_url='/users/login/')`. `next` is ignored. Logout (POST `/users/logout/`) calls `logout()` and redirects to login.

### Flow 2 — Fetch a problem from Polygon
- **User does:** enters a Polygon problem ID, clicks Fetch Problem.
- **Sent:** JS shows a spinner, then POSTs `problem_id` + CSRF to `/`.
- **Backend (`index`):**
  1. Loads existing tags for the picker; creates `PolygonAPI()`.
  2. If the problem is **already in the DB**: calls `problem.updateWorkingCopy`, then `problem.solutions` and `problem.viewSolution` for the main solution (tag `MA`, else first), and restores its stored difficulty and tags.
  3. Inside `transaction.atomic()`: `get_problem_info()`, then `download_and_extract_package()` (newest `standard` package → ZIP → `problem.html`), then `parse_problem_html()` (title, legend, input/output spec, notes).
  4. `get_all_test_cases()`: list tests, then input and answer for each test. A failed test becomes empty input/output instead of aborting. Result cached in Redis for 30 min.
  5. Resolves the checker (`std::` and `.cpp` stripped; non-standard → `custom`), counts tests, builds `fetched_problem`. `constraints` and `editorial` are always empty.
- **External APIs:** `problem.info`, `problem.packages`, `problem.package`, `problem.tests`, `problem.testInput`, `problem.testAnswer`, `problem.checker` (some called twice); plus `updateWorkingCopy`, `solutions`, `viewSolution` for existing problems.
- **DB/storage:** read-only on `Problem`; **nothing written** except Redis keys.
- **User sees:** title, DB id and Algopath link (or “migrate first”), slug, difficulty and tag pickers, limits, checker, test count, statement/formats/notes, a test-case table with previews, the main solution, and the action buttons. Statement HTML is auto-escaped, so tags show as literal text.

### Flow 3 — Migrate the problem to the database
- **User does:** picks a difficulty (required) and tags, clicks Create/Update problem in Database.
- **Sent:** `problem_id`, `migrate_to_db=1`, `difficulty`, one hidden `tags` input per tag (new tags go in `tags` too; `new_tag` is never sent by this template).
- **Backend (`index`):**
  1. Runs the fetch block (test cases read from Redis if cached).
  2. Rejects empty difficulty (transaction rolls back).
  3. Updates the `Problem` with that `polygon_id`, or creates it, from the fetched fields.
  4. Tags: `extra_tags.clear()`, then `get_or_create` + `add` for each submitted tag (replaces old tags).
  5. Sample tests (`useInStatements`): rstrip, skip empties, update `SampleTestCase` by position or create (order 1, 2, …). Extra old rows are not deleted.
  6. Fetches the main solution for display.
- **External APIs:** as Flow 2, plus `solutions` and `viewSolution`.
- **DB:** writes `Problem`, `ProblemTag`, the tags join table, `SampleTestCase`, all atomically. A duplicate `slug` (same title) or `polygon_id` makes it fail and roll back.
- **User sees:** blue “Problem '…' created/updated” alert, DB id and link filled in, tag chips, solution shown, other buttons enabled.

### Flow 4 — Migrate test cases to the database
- **User does:** clicks Migrate Test Description to Database (enabled only once the problem is in the DB).
- **Sent:** `problem_id`, `migrate_test_cases_to_db=1`.
- **Backend (`index`):**
  1. Runs the fetch block; errors if the `Problem` does not exist.
  2. Gets tests from Redis or Polygon.
  3. For each test: rstrip input/output, **skip if either is empty**, **truncate each to 260 characters**, then overwrite the existing `ProblemTestCase` at that position or create one (`order` 1, 2, …). Leftover old rows stay.
- **External APIs:** same fetch calls as Flow 2.
- **DB:** writes `ProblemTestCase` only (not `SampleTestCase`, not `test_case_count`).
- **User sees:** green “Test cases description migrated…” alert. The table still shows full Polygon data, not what was stored (check `/admin/`). The truncation is silent.

### Flow 5 — Upload test cases to cloud storage
- **User does:** clicks Migrate Test Cases to Storage (enabled only once the problem is in the DB).
- **Sent:** `problem_id`, `migrate_to_azure=1`.
- **Backend (`index` → `PolygonAPI.migrate_to_storage`):**
  1. Requires the `Problem`; its **DB id** names the files.
  2. `get_storage()` builds `S3Storage` or `AzureStorage` from `STORAGE_PROVIDER`.
  3. Gets tests from Redis or Polygon.
  4. `storage.delete_problem_files(id)` wipes `test_cases/{id}/`, then each test with both input and output is uploaded via `upload_test_case(id, idx, in, out)`. `idx` is the 1-based position in Polygon's list, so skipped tests leave gaps.
  5. For a custom checker: fetch its source (`problem.viewFile`), compile with `g++`, upload the binary (or the `.cpp` if compiling fails).
  6. The fetch block then runs to redisplay the page.
- **External APIs:** Polygon as above, plus `problem.viewFile`; S3 (`PutObject`, `ListObjectsV2`, `DeleteObjects`) or Azure Blob.
- **DB/storage:** DB read-only; storage is delete-then-write.
- **User sees:** green “Test cases migrated to storage (provider) successfully.” Verify by listing `test_cases/<Problem.id>/` in the bucket and opening `01` and `01.a`.
- **Failure:** on any exception the DB rolls back, Redis keys are cleared, and files are deleted if the upload had completed. Storage is not transactional, so a mid-upload failure leaves partial files.
- **Quirk:** `delete_problem_test_case_cache` uses the key prefix `oj_dev_with_redis_storage_test_cases_…`, not the one this app writes, so it clears nothing and the 30-minute cache stays in use.

**Database vs storage:** the DB holds searchable metadata plus 260-character previews of tests, updated in place. Storage holds the **full** input/output files (and the checker), wiped and rewritten each time.

## 3. External integrations

### 3.1 Polygon API (`problems/polygon_api.py`)
Every call is a POST to `https://polygon.codeforces.com/api/<method>`. **Auth** (`_generate_api_sig`): add `apiKey` and `time` to the params, sort and URL-encode them, pick a random 6-char `rand`, compute `SHA-512(rand/<method>?<params>#<secret>)`, and send `apiSig = rand + hex`. The secret is never sent. JSON replies are `{status, result, comment}`; `FAILED` raises. `testInput`, `testAnswer`, `viewSolution` and `viewFile` return plain text; `problem.package` returns a ZIP.

Endpoints used: `problem.info`, `problem.packages`, `problem.package`, `problem.tests`, `problem.testInput`, `problem.testAnswer`, `problem.checker`, `problem.solutions`, `problem.viewSolution`, `problem.updateWorkingCopy`, `problem.viewFile`.

### 3.2 Cloud storage (`problems/storage/`)
`TestCaseStorage` (`base.py`) defines `upload_test_case`, `upload_file`, `delete_problem_files` and the naming helpers. The migration code only uses this interface; `get_storage()` picks the provider. To add one, subclass it and add a branch there.

| Provider | Class | Env vars |
|---|---|---|
| S3 / S3-compatible | `S3Storage` | `AWS_S3_BUCKET_NAME`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, optional `AWS_S3_ENDPOINT_URL` |
| Azure Blob | `AzureStorage` | `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_USERNAME`, `AZURE_PASSWORD`, `AZURE_CONTAINER_NAME` |

Set `STORAGE_PROVIDER=s3|azure` (default `azure`).

**Layout** (same for all providers):
```
test_cases/{Problem.id}/{NN}      input of test NN (1-based, zero-padded to 2 digits)
test_cases/{Problem.id}/{NN}.a    expected output
test_cases/{Problem.id}/custom_checker[.exe|.cpp]   only for custom checkers
```
Files are UTF-8; samples are included in Polygon order. S3 errors propagate; the Azure wrapper logs and swallows upload errors.

### 3.3 Redis
Keys `polygon_migration_test_cases_{polygon_id}_count` and `…_test_{n}`, 30-minute TTL. **Fetch** always refreshes the cache; the migrate actions reuse it, so Polygon edits made in the last 30 minutes can be migrated stale. All Redis errors are logged and ignored.

## 4. Other env vars
`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DB_NAME/USER/PASSWORD/HOST/PORT`, `REDIS_HOST/PORT/PASSWORD/SSL`, `POLYGON_API_KEY/SECRET`, optional `CUSTOM_CHECKER_DIR`. `.env` is git-ignored; `.env.example` lists everything.

## 5. Code map
- Button behaviour: `problems/views.py` → `index()`; statement parsing: `parse_problem_html()`
- Polygon calls and signing: `problems/polygon_api.py`
- Storage layout / providers: `problems/storage/base.py`, `__init__.py`
- Login rules: `users/views.py`, `users/backends.py`
- UI and JS: `problems/templates/problems/index.html`
