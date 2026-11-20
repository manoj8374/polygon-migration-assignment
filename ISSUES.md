# Issues Analysis

Line numbers refer to the original starter code (commit `ab4a864`, branch `main`).
"Tested" means I ran it and saw the result. "From code" means I concluded it by reading the code.

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| Product Issues | 0 | 2 | 1 | 0 | 3 |
| Code Issues | 0 | 2 | 1 | 0 | 3 |

---

## Product Issues (1 = highest priority)

### [P1] Re-migrating a problem leaves stale test cases in the database but still reports success

**Severity**: High

**Location**: `problems/views.py:455-464` (sample tests), `problems/views.py:530-546` (test cases)

**Problem**:
When a problem is migrated again, the code reads the existing rows and overwrites them by position
(`if idx < len(existing_test_cases): update ... else: create`). Nothing ever deletes the rows that are
no longer in Polygon. If a setter removes tests on Polygon, the old rows at the end stay in the database.
Tested (simulated): the database had 15 `ProblemTestCase` rows and Polygon returned 8 tests. After
"Migrate Test Description to Database" the page said "Test cases description migrated to database
Successfully." and there were still 15 rows (8 updated, 7 untouched). The cloud-storage upload, by contrast, deletes
the whole folder first, so storage ends up correct and the database does not.

**Impact**:
The staff user is told everything worked, but the database holds test cases that no longer exist in
the real problem. If the platform judges submissions against these rows, they can be wrong. The database and
storage disagree, and nothing on the page warns about it. It is hard to notice and hard to debug later.

**Recommended improvement**:
Make the migration replace the set of tests, not patch by position: delete the problem's existing
`SampleTestCase`/`ProblemTestCase` rows that are not in the new list (or delete and re-insert inside the
transaction). Show a short summary before and after ("12 tests will replace 20; 8 will be removed") and
ask for confirmation when tests are removed. Report the real counts in the success message.

**Priority rationale**:
It silently produces wrong data, which is worse than a visible failure, and it happens in the main
"update an existing problem" flow. It ranks first because the user gets no signal that anything is wrong
and the damage reaches whatever uses this data downstream.

---

### [P2] "Migrate Test Description to Database" actually copies test data, and truncates it

**Severity**: High

**Location**: `problems/templates/problems/index.html:314` (button label), `problems/views.py:533-541`

**Problem**:
The button is called "Migrate Test Description to Database" and the success message says "Test cases
description migrated". In reality it copies each test's input and output, but cuts both to the first 260
characters (`input_data[:260]`, `output_data[:260]`) and strips trailing whitespace. Tests with an empty input or
output are skipped completely. Nothing on the page says any of this. (From code.)

**Impact**:
The label makes staff think they are copying a short description, or that the full tests are copied. The
database copy is incomplete for any large test, while the storage copy is full, so the two copies of the same
test differ. A user who later relies on the database copy gets wrong data and has no clue why.

**Recommended improvement**:
Name the button for what it does (for example "Save test previews to database"), say in the UI that
inputs and outputs are cut to 260 characters, and show which tests were truncated or skipped in the result
message. Better, store the full data (or only a reference to the storage object) so there is one source of truth.

**Priority rationale**:
It affects every migration, not only unusual cases, and it misleads users about what was saved. It ranks
below P1 because P1 makes the data wrong silently, while here the truncation is at least intentional and the
fix is mainly clearer wording and reporting.

---

### [P3] A second problem with the same title cannot be migrated, and the error is a raw database message

**Severity**: Medium

**Location**: `problems/views.py:298` (`slug = slugify(title)`), `problems/models.py:58` (`slug` is unique), `problems/views.py:382` and `:569`

**Problem**:
The slug is generated from the title and must be unique, and the user cannot change it. Tested (Django
test client against the real view, rolled back afterwards): the first "Two Sum" (Polygon 635336) migrated fine. The
second "Two Sum" (Polygon 635338) failed and the page showed: "Migration failed and all changes have been rolled
back. Reason: duplicate key value violates unique constraint "problems_problem_slug_key" DETAIL: Key
(slug)=(two-sum) already exists." No row was created for the second problem.

**Impact**:
Two legitimately different problems with the same or similar title cannot both be migrated. The staff user
sees a database error they cannot act on and has no field to fix it. They have to change the title on Polygon,
which may not be allowed or desirable.

**Recommended improvement**:
Let the user see and edit the slug before migrating, or generate a unique one automatically (for example
`two-sum-2`). Catch the unique-constraint error and show a clear message ("A problem with this title already
exists: <link>. Choose a different slug").

**Priority rationale**:
It completely blocks migration for affected problems, but it only happens on a title collision, so it affects
fewer migrations than P1 and P2. It stays in the top three because it is a hard stop with no workaround inside the tool.

---

## Code Issues (1 = highest priority)

### [C1] One view (`index`) does everything and repeats itself

**Severity**: High

**Location**: `problems/views.py:88-583`; repeated blocks at `:262`, `:440`, `:519`; solution lookup at `:155-183` and `:485-509`

**Problem**:
A single function of about 500 lines handles four different actions (fetch, create/update problem,
migrate test cases to the database, upload to storage). The same "Redis, else Polygon, then store in Redis" block
is copied three times, and the main-solution lookup is copied twice. On every POST, whatever button was clicked,
it also re-downloads the package, re-parses the HTML and re-fetches test cases and checker info for display
(`:249-322`). There are no service functions and no tests (`problems/tests.py` is empty).

**Impact**:
A new engineer cannot find where one behavior lives, and every change must be made in several places and
risks breaking another action. The repeated Polygon calls make every click slow and expensive. Nothing can be
tested on its own.

**Recommended improvement**:
Split the view into one small view or handler per action and move the logic into service functions
(`fetch_problem`, `save_problem`, `save_test_cases`, `upload_to_storage`). Put the repeated "get test cases
(cached)" code in one function. Only fetch from Polygon what the clicked action needs. Add unit tests for the
services.

**Priority rationale**:
It is the root cause of most other problems and the biggest obstacle to maintaining or extending the tool,
which is what this review is about. It comes first among code issues because C2 and C3 are hard to fix cleanly
until this is split up.

---

### [C2] Syncing test cases is positional and the numbers come from different sources

**Severity**: High

**Location**: `problems/views.py:455-464`, `:530-546`, `:322`, `:538-563`

**Problem**:
Existing rows are matched to new tests by list position, extra rows are never removed (tested, see P1),
and `order` is only increased for tests that were kept, while the storage upload numbers tests with
`enumerate` over all of them, so skipped tests make the two numberings disagree (from code).
`Problem.test_case_count` is set from a fresh Polygon call (`len(api.get_test_cases(...))`, `:322`), but the
rows are written from the cached list (up to 30 minutes old), so the count and the rows can differ.

**Impact**:
The data model has no single, reliable idea of "the tests of this problem". The count, the rows and the storage
files can each be different, and any code that trusts one of them can be wrong.

**Recommended improvement**:
Treat the Polygon test list as the single source of truth for one migration run. Compute the count from
the same list that is written, key tests by their Polygon index instead of position, delete rows that are not in
the list, and use the same numbering for the database and storage.

**Priority rationale**:
It is the cause of the data problem in P1 and affects data integrity. It is below C1 because it is
one specific piece of logic, while C1 is the structure that makes this code hard to find and fix.

---

### [C3] External side effects inside a database transaction, failures swallowed, and a cache key that never matches

**Severity**: Medium

**Location**: `problems/views.py:186` (`transaction.atomic`), `:238` and `:572` (`api.delete_azure_blob`); `problems/polygon_api.py:311`, `:401`, `:794`, `:813-820` vs `:865`

**Problem**:
All work, including uploads to Azure and writes to Redis, runs inside `transaction.atomic()`, but only
the database can be rolled back, yet the error message says "all changes have been rolled back". The cleanup
calls `api.delete_azure_blob(...)`, a method that does not exist on `PolygonAPI`, so the cleanup itself would
fail (from code). Errors are swallowed: `get_test_cases` returns `[]` on any exception (`:311`),
`get_all_test_cases` turns a failed fetch into empty input and output (`:401`), and the uploader then skips such
tests with only a log warning (`:794`) and still reports success. `delete_problem_test_case_cache` deletes keys
matching `oj_dev_with_redis_storage_test_cases_*`, but this app stores keys as
`polygon_migration_test_cases_{polygon_id}`, so it never clears its own cache.

**Impact**:
A failed or partial migration can look successful, and the "rolled back" message can be false for storage
and Redis. Missing tests are only visible in a log file. A stale cache can be reused for up to 30 minutes.

**Recommended improvement**:
Do the database work in the transaction and the external work separately, with explicit, tested cleanup.
Let Polygon errors propagate (or collect them) and fail the migration when any test cannot be fetched. Report
how many tests were uploaded versus expected. Use one cache key helper everywhere, and invalidate it
when a migration finishes.

**Priority rationale**:
It can make failures invisible, but it needs a failure to happen first (a network error, an empty test),
and it is easier to fix once C1 and C2 are done, so it is third.

---

## Edge Case Analysis

### Q1: 0 sample test cases and 15 regular test cases

**Migration actions**: "Create/Update problem in Database", "Migrate Test Description to Database", then
"Migrate Test Cases to Storage", on Polygon problem 635342 ("Zero Samples": 15 tests, none marked
"Use in statements").

**What happened (Tested)**:
All three actions succeeded with no error. After them, the database had the problem with
`test_case_count = 15`, **0** `SampleTestCase` rows, **15** `ProblemTestCase` rows (none with `is_sample` set),
and storage had **30** objects (`01` to `15` and their `.a` files) under `test_cases/2/`. On the fetch page all
15 tests showed "Sample Test Case: No".

**Why (code)**:
Sample tests are chosen with `is_sample = test.get('useInStatements', False)` in
`polygon_api.py:get_all_test_cases` and then filtered with `sample_tests = [t for t in test_cases if
t.get('is_sample')]` in `views.py:453`. With none marked, the list is empty, so the sample loop creates nothing.
The regular test loop (`views.py:530`) does not depend on samples, so all 15 are saved, and the storage upload
sends every test with both input and output.

**Conclusions from code only (From code)**:
Because the sample table is empty, anything that shows examples from `SampleTestCase` would show no
examples for this problem. The tool gives no warning that a problem has no samples.

---

### Q2: 20 tests migrated, 8 removed on Polygon (12 remain), migrated again

**Migration actions**: "Create/Update problem in Database", "Migrate Test Description to Database", and
"Migrate Test Cases to Storage", run again after the setter's change.

**What happened (Tested, simulated)**:
I did not delete tests on Polygon. I simulated it: the database held 15 `ProblemTestCase` rows and the
Polygon list was cut to 8. After "Migrate Test Description to Database" the message said "Successfully" and
there were still **15** rows; the first 8 were overwritten and the last 7 were left unchanged.

**Why (code)**:
`views.py:530-546` updates existing rows by position and only creates rows when `idx` is past the end of the
existing list. There is no delete step. So for 20 rows and 12 tests, rows 1-12 are updated and rows 13-20 stay.

**Conclusions from code only (From code)**:
- The database would end with 20 rows, while "Create/Update problem in Database" sets `test_case_count` to 12
  (a fresh `len(api.get_test_cases(...))` call at `:322`), so the count and the rows disagree.
- Storage ends up correct: `migrate_to_azure_blob` (now `migrate_to_storage`) deletes everything under
  `test_cases/{id}/` first and then uploads the 12 tests, so storage has 12 pairs.
- If the setter's change is less than 30 minutes old and the page was fetched before it, the migrate buttons read the
  Redis cache (`:440`, `:519`), so they could still use the old 20 tests.

---

### Q3: Two different Polygon problems titled "Two Sum"

**Migration action**: "Create/Update problem in Database", first on Polygon 635336 and then on Polygon 635338.

**What happened (Tested)**:
I ran this with the Django test client against the real view, then rolled the database back. The first
migration succeeded ("Problem 'Two Sum' created in database successfully", slug `two-sum`). The second
failed with: "Migration failed and all changes have been rolled back. Reason: duplicate key value violates unique
constraint "problems_problem_slug_key" ... Key (slug)=(two-sum) already exists." No row was created for the
second problem.

**Why (code)**:
The slug is computed from the title (`views.py:298`, `slugify(title)`), and `Problem.slug` is `unique=True`
(`models.py:58`). The lookup for an existing problem uses only `polygon_id` (`views.py:354`), so the second
problem is treated as new, `Problem.objects.create(...)` (`:382`) hits the unique constraint, and the generic
`except` (`:567-569`) shows the raw database error.

**Conclusions from code only (From code)**:
The second problem cannot be migrated, and so cannot reach the test-case or storage steps, until its title
changes on Polygon, because the storage action requires the problem to exist in the database first.

---

### Q4: Data discarded by "Migrate Test Cases to DB"

**Migration action**: "Migrate Test Description to Database" (the button that migrates test cases to the database).

**What is lost (From code)**:
In `views.py:533-541`: (1) each test's input and output is cut to the first **260 characters**; (2) trailing
whitespace is removed from both (`rstrip()`); (3) any test whose input or output is empty is **skipped entirely**
(`if input_data and output_data`). Descriptions and the sample flag are kept.

**Why this can cause problems**:
- A test with large input or output is stored incomplete, so the database copy cannot be used to run or compare
  that test. Storage keeps the full data, so the two copies of the same test no longer match.
- Removing trailing whitespace and newlines can change a test that a strict checker compares exactly.
- Skipped tests (for example an empty expected output) disappear from the database, and `order` only counts
  kept tests, while storage numbers every test by position, so the numbering of the database and storage can
  differ after a skip.
- None of this is shown to the user: the message only says "Test cases description migrated to database
  Successfully."

I did not run a test with input over 260 characters, so these points come from code reading, not from running it.

---

## Notes

- **Setup blocker found and fixed:** the `Problem` model defines `notes` and `genie_chat`, but the initial
  migration did not create them, so the first fetch failed with "column problems_problem.notes does not exist".
  I added migration `0002_problem_genie_chat_problem_notes.py` (included in the PR).
- **Environment gotcha:** `load_dotenv` does not override variables already in the process, so after changing
  `STORAGE_PROVIDER` in `.env` the dev server had to be fully restarted, not just auto-reloaded.
