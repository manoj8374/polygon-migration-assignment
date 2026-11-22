# Issues Analysis

Line numbers refer to the current code in this branch, after my fixes, not the original starter code.

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| Product Issues | 0 | 2 | 1 | 0 | 3 |
| Code Issues | 0 | 2 | 1 | 0 | 3 |

---

## Product Issues

### [P1] If one test fail to download, it is dropped silently and page still says "Success"

**Severity**: High

**Location**: Test case table and the green Success box. Code: `problems/polygon_api.py:399-402`, `problems/views.py:493`.

**Problem**: Fetch downloads every test from Polygon. If one download fails, the code don't stop. It saves that test as empty and continues. I tried with a 5 test problem where test 2 failed once. The page showed test 2 blank with no error. Database step said "Successfully" and saved only 4 rows. Storage said "Uploaded 4 of 5 ... because the input or output was empty", which is not true, the test just did not download. (This storage message is from my fixed code. The starter code skipped the test with only a log line and showed a normal success.)

**Impact**: User thinks migration is done but one test is missing. A wrong solution can pass now.

**Recommended improvement**: Treat failed download as an error. Retry it, and if still fails then stop and tell the user. Don't show green when tests are missing.

**Priority rationale**: First, because it is the only one where data is wrong and user is told it is fine. P2 and P3 the user can see, this one he can't.

---

### [P2] Page does not tell which steps are already done

**Severity**: High

**Location**: Main page after Fetch, `problems/templates/problems/index.html:157-168`.

**Problem**: Full migration needs three buttons. Each button only tell about itself. The page never shows which steps was already run for a problem. After Create/Update it says "created successfully", so it feels like everything is done. I found this in my own database: problem 5 has `test_case_count = 13` and 26 files in S3, but 0 rows in test case table. The page looks same as a complete problem. There is also no list of migrated problems.

**Impact**: Every user, every migration. A step gets missed and nobody knows. To check, you have to open admin and the bucket yourself.

**Recommended improvement**: Show a small status box: problem saved or not, how many test rows, how many files in storage. One "Migrate everything" button will also help.

**Priority rationale**: It happens in every migration and I have real proof. It is below P1 because saved data is correct, only incomplete. Above P3 because unfinished problem is worse than slow page.

---

### [P3] Every click takes around 40 seconds and only a spinner is shown

**Severity**: Medium

**Location**: All four buttons. Spinner text at `problems/templates/problems/index.html:423`.

**Problem**: I timed it on a problem with 13 tests. Fetch took 37 seconds, Create/Update 37 seconds, and the test description step 42 seconds. Fetch being slow is okay, but the other buttons also downloads the package again and ask Polygon for same things the page was already showing. So even a small problem is more than two minutes of waiting. With more tests it become worse. While waiting there is only a spinner, no progress and no cancel.

**Impact**: Everyone, on every click. With a big problem you can't tell if it is slow or stuck, so people reload.

**Recommended improvement**: Download from Polygon one time on Fetch and reuse it for other buttons. Run long work in background and show progress like "test 34 of 120".

**Priority rationale**: Nobody can use the tool without facing this, so it is in top 3. But it only waste time, it does not break data, so it is third.

---

## Code Issues

### [C1] One function is doing everything

**Severity**: High

**Location**: `problems/views.py:89-578`, `problems/urls.py:7`. Copies at `views.py:217`, `:395`, `:474` and `problems/polygon_api.py:654`.

**Problem**: The whole app is one view of around 490 lines on one URL. It checks hidden form fields to know which button was pressed, then does fetch, save problem, tags, samples, test cases and storage upload in one big `try` block. Because nothing is separated, code is copied. The "get tests from Redis or Polygon" block is there four times (three in `views.py` and one in `migrate_to_storage` in `polygon_api.py`) and the solution lookup two times. There is no Django form. Also `problems/tests.py` has 19 tests but all are for storage classes, none for the view.

**Impact**: To change one action you must read all of them. A fix in one copy is easy to miss in other copies. Every change is tested by clicking only.

**Recommended improvement**: One URL and one small view for each action. Move the real work into simple functions like `save_problem` and `save_test_cases`, and write tests for them.

**Priority rationale**: First, because it makes every other fix harder. C2 and C3 can be fixed in one place, but first you need to find that place.

---

### [C2] Models do not enforce their own rules

**Severity**: High

**Location**: `problems/models.py:38-80`, `:91-142`, `problems/admin.py:58`, `problems/views.py:269`.

**Problem**: The models look strict but database accept almost anything.
- `difficulty` has `choices` (`models.py:59`) but they only work with forms. I posted `difficulty=banana` and it got saved with success message.
- Test rows have no identity. `order` is not unique and Polygon test number is not stored, so rows are matched by position only.
- Samples are stored in two places, `SampleTestCase` table and `is_sample` flag. Nothing keeps them same.
- `test_case_count` is a separate number. In my Q2 test it was 12 but rows were 20.
- Checker list is written two times (`models.py:43-55`, `views.py:269`).

**Impact**: Bad data can come from view, admin or any script. Count, rows and samples can all say different things.

**Recommended improvement**: Add check constraints and a unique constraint on (`problem`, `order`). Save Polygon test number. Keep samples in one place and compute the count from rows.

**Priority rationale**: Database is the output of this tool, so it must be trusted. It is second because the fix is small, while C1 blocks everything.

---

### [C3] `PolygonAPI` is four things in one class with no safety for outside services

**Severity**: Medium

**Location**: `problems/polygon_api.py`, mainly `:96`, `:187`, `:602`, `:714`, `:859`. Also `PolygonMigration/settings.py:148-151`.

**Problem**: This one class is the Polygon client, the Redis cache, the C++ compiler and the storage upload logic.
- No timeout on `requests.post` (`:96`, `:187`). If Polygon don't answer, the request hangs forever.
- Redis connection code is pasted four times, and `settings.py` has no `CACHES` setting.
- Keys are deleted by prefix (`:714`, `:859`). I checked with dummy keys: clearing problem 5 also removed 50 and 57.
- The prefix `oj_dev_with_redis_storage_test_cases_` looks like it belong to another system, because this app never writes keys with that prefix.
- Checker is compiled on web server, so on Windows it uploads `custom_checker.exe` (`:602`).

**Impact**: You can't change or test one job without the other three. One slow Polygon call can block a worker, and clearing cache can remove other problem's entries.

**Recommended improvement**: Split into a `PolygonClient` with timeouts, a `TestCaseCache` that deletes exact keys, and a separate checker builder.

**Priority rationale**: Everything goes through this class and it is the biggest file. It is third because it can be split without touching database or the page.

---

## Edge Case Analysis

### Q1

> A Polygon problem has 0 sample test cases but 15 regular test cases. What happens when you migrate this problem?

**Migration action**: Fetch, then all three migrate buttons.

**What happened (Tested)**: Everything succeeded with no warning. `test_case_count` was 15. Sample table had 0 rows, test case table had 15 rows (none marked sample) and storage had 30 files, `01` to `15` with `.a` files.

**Why (code)**: A test is sample only if Polygon marks it "use in statements" (`polygon_api.py:376`). The view filters on that (`views.py:408`) and gets empty list, so sample loop does nothing. The other two steps don't check this flag.

**From code only (not tested)**: User is never told that problem has no samples, so it can go live without examples.

---

### Q2

> A problem is migrated with 20 test cases. Later, the problem setter removes 8 test cases on Polygon (now 12 remain). The problem is re-migrated. What happens?

**Migration action**: All three buttons with 20 tests, then again after tests were reduced to 12.

**What happened (Tested)**: All steps said success but database was wrong. `test_case_count` became 12, but test case table still had 20 rows. Rows 1 to 12 had new content and rows 13 to 20 was the old tests. Sample table kept 3 rows though only 2 samples were left. Storage was correct with 24 files. Also if I did not press Fetch again, it used the old 20 tests, because fetched tests are reused for 30 minutes.

**Why (code)**: Rows are matched by position. If a row exist it is updated, else created (`views.py:499-518`). Nothing is ever deleted. The count comes from a separate Polygon call (`:277`). Storage deletes files that was not in the new upload (`polygon_api.py:696`), so it is correct.

**From code only (not tested)**: Anything reading the table will see 20 tests, and rows 13 to 20 have no file in storage.

---

### Q3

> Two different Polygon problems have the exact same title: "Two Sum". You migrate the first one successfully. Then you try to migrate the second one. What happens?

**Migration action**: Fetch and Create/Update for first problem, then for second.

**What happened (Tested)**: First was created with slug `two-sum`. Second failed, no row was created, and page showed: "Migration failed and all database changes have been rolled back. Reason: duplicate key value violates unique constraint "problems_problem_slug_key" DETAIL: Key (slug)=(two-sum) already exists."

**Why (code)**: Slug is made from title (`views.py:253`) and must be unique (`models.py:58`). The view finds existing problem only by Polygon ID (`:309`), so it tries to create new one (`:337`) and database rejects it. Error handler just prints the database message.

**From code only (not tested)**: Second problem is stuck. Other buttons stay disabled and there is no field to change slug. Only way is to rename it on Polygon.

---

### Q4

> When test cases are saved to the database via "Migrate Test Cases to DB", some data is intentionally discarded. What data is lost? Why might this cause problems?

**Migration action**: "Migrate Test Description to Database" on a problem with four tests I made for this: a normal one, one with 801 character input, one with empty output, one with spaces at end of output.

**What is lost (Tested)**: Page only said "migrated to database Successfully", but:
- Everything after 260 characters. The 801 character input was saved as 260. Storage had full 801.
- Whitespace at the end. `ok` with three newlines was saved as `ok`.
- The whole test if input or output is empty. Table had rows 1, 2, 3 but storage had `01`, `02`, `04`.

**Why (code)**: `views.py:488-489` strips both values, `:493` skips empty tests and `:495-496` cuts to 260 characters.

**Why it could cause problems**: Database copy of a big test is not the real test and nothing marks it as cut. An exact checker can give different result without the trailing whitespace. After a skipped test the numbers don't match anymore, row 3 in database is file `04` in storage.

---

## Notes

- For cases I could not make on a real Polygon problem (failed download, removed tests, same titles, long tests) I ran the real view code with a fake Polygon and rolled back the database.
- I did not put Q2, Q3 and Q4 in product top 3 because each need a special situation. The three I picked comes in a normal migration.
- Starter code did not run until I added migration `0002` for `notes` and `genie_chat`.
- Create/Update overwrites a problem without warning and always makes constraints and editorial blank.
