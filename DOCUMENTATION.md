# Functionality Documentation

This tool is for staff users. It copies a problem from Polygon into our PostgreSQL database and uploads its test files to cloud storage (S3 or Azure). I wrote this for the current code in this branch, after my storage fixes.

One thing to know before reading: the whole tool is one page. Every button sends a POST to `/` and all of them are handled by `index()` in `problems/views.py`. The page is `problems/templates/problems/index.html`. Each form has a hidden field, and that is how `index()` knows which button was pressed.

## 1. User Flows

### Flow 1: User login

**What the user does**: Opens `/`, gets redirected to `/users/login/`, enters email and password and clicks **Login**.

**What the frontend sends**: `POST /users/login/` with `email`, `password` and `csrfmiddlewaretoken`. The form is in `users/templates/users/login.html`.

**What the backend does (in order)**: This is `login_view()` in `users/views.py`.
1. If somebody is already logged in, it logs them out first.
2. It calls Django's `authenticate()`. That goes to `EmailBackend` in `users/backends.py`, which finds the user by email and checks the password. It also rejects users who are not active (`is_active` false).
3. If user is not found or password is wrong, message is "Invalid email or password."
4. If user is not staff (`is_staff` false), message is "You do not have staff access."
5. Otherwise it calls `login()` and redirects to `/`. The `?next=` in the URL is ignored.

**External APIs called**: None.

**Database / storage read or written**: Reads the user from `users_user`. On success it writes a session row in `django_session` and updates `last_login`. Session stays for one day (`SESSION_COOKIE_AGE` in `settings.py`).

**What the user sees**: The main page "Hatsu: Problem Migrator" with a box for the Polygon problem ID. If login fails, the same login page with a red message.

Also good to know: `index()` has `user_passes_test` on it, so anyone who is not a logged in staff user is sent to `/users/login/`. The **Logout** button posts to `/users/logout/` (`logout_view()`), which ends the session and goes back to login page.

---

### Flow 2: Fetching a problem from Polygon

**What the user does**: Types a Polygon problem ID and clicks **Fetch Problem**.

**What the frontend sends**: `POST /` from the form `fetchForm` with `problem_id` and the CSRF token. The JavaScript shows a loading overlay before sending.

**What the backend does (in order)**: `index()` does the work, using the `PolygonAPI` class from `problems/polygon_api.py`.
1. Loads all tags from the database for the tag picker. This happens on every request.
2. Checks if a `Problem` with this `polygon_id` is already saved. If yes, the page uses its saved difficulty and tags, and the main solution is fetched from Polygon.
3. Gets time limit and memory limit with `get_problem_info()`.
4. `download_and_extract_package()` downloads the latest standard package, unzips it in a temp folder and reads `problem.html`.
5. `parse_problem_html()` picks the title, legend, input format, output format and notes from that HTML.
6. `get_all_test_cases()` gets the test list and then downloads input and answer of each test, one by one. This is the slow part.
7. `store_test_cases_in_redis()` keeps these tests in Redis for 30 minutes, so the next buttons don't need to download again.
8. Gets the checker name. A standard one like `std::ncmp.cpp` becomes `ncmp`. Anything not in the known list becomes `custom`.
9. Counts the tests with `get_test_cases()` and renders the page.

**External APIs called**: Polygon: `problem.info` (two times), `problem.packages`, `problem.package`, `problem.tests` (two times), `problem.testInput` and `problem.testAnswer` (once for each test), `problem.checker` (two times). If the problem is already in database, also `problem.updateWorkingCopy`, `problem.solutions` and `problem.viewSolution`.

**Database / storage read or written**: Only reads `Problem` and `ProblemTag`. Nothing is written to database or storage here. The fetched tests go to Redis.

**What the user sees**: Title, slug, limits, checker, test count, the statement parts, a table of all tests (first 50 characters, with **View Full** button for long ones) and the migrate buttons. If the problem is not in database yet, page says "Please migrate to database first." and the two test buttons are disabled. If something fails, a red box says "Migration failed and all database changes have been rolled back. Reason: ..." and the saved tests for this ID are removed from Redis.

---

### Flow 3: Migrating a problem to the database

**What the user does**: After fetch, selects a difficulty, picks tags if needed, and clicks **Create/Update problem in Database**.

**What the frontend sends**: `POST /` from the form `migrate-db-form` with `problem_id`, `migrate_to_db=1`, `difficulty` and one `tags` field per selected tag. The JavaScript copies the dropdown value and tags into hidden fields, and it blocks the submit if difficulty is not selected.

**What the backend does (in order)**: Still `index()`, and everything is inside one `transaction.atomic()` block.
1. Runs the same fetch as Flow 2. Only difference is tests are taken from Redis if they are still there.
2. If `difficulty` is empty it stops. The error handler wraps the message, so the user sees "Migration failed and all database changes have been rolled back. Reason: Please select a difficulty level before migrating to database."
3. Looks for a `Problem` with same `polygon_id`. If found it **updates** that row, if not it **creates** a new one. Slug is made from the title with `slugify()`.
4. Removes all tags of the problem and adds the selected ones again. A tag that doesn't exist is created.
5. Takes the tests marked as sample on Polygon and saves them as `SampleTestCase` rows. Existing rows are overwritten by position and extra ones are created. Samples with empty input or output are skipped.
6. Fetches the main solution from Polygon so the page can show it.

**External APIs called**: Polygon, same as Flow 2 but without the per-test calls when tests are in Redis. Plus `problem.solutions` and `problem.viewSolution`.

**Database / storage read or written**: Writes `problems_problem` (title, slug, difficulty, statement, input and output format, limits, checker type, test count, notes). Constraints and editorial are always saved as empty. Also writes `problems_problemtag`, the problem-tag link table and `problems_sampletestcase`. Nothing in storage.

**What the user sees**: A blue box: "Problem '<title>' created in database successfully." or "... updated in database successfully.", with one more line if a custom checker was found. Page now shows the database problem ID and the two test buttons get enabled. Migrating the same Polygon ID again is okay, it just updates the row. It fails only when a **different** problem already has the same slug (same title). Then a red box shows the database error and nothing is saved.

---

### Flow 4: Migrating test cases to the database

**What the user does**: Clicks **Migrate Test Description to Database**. It is below the test table, and disabled until Flow 3 is done.

**What the frontend sends**: `POST /` from the form `migrate-test-cases-form` with `problem_id` and `migrate_test_cases_to_db=1`.

**What the backend does (in order)**: `index()` again, in the same transaction block.
1. Runs the same fetch as Flow 2 (tests from Redis if present).
2. Finds the `Problem` by `polygon_id`. If missing, it stops and the user sees "Migration failed and all database changes have been rolled back. Reason: Please migrate the problem to the database first."
3. Takes tests from Redis, or from Polygon if Redis has none.
4. For each test: removes whitespace at end of input and output, skips the test if any of them is empty, and cuts both to first 260 characters.
5. Saves each test as a `ProblemTestCase` row with input, output, sample flag, description and order. Existing rows are overwritten by position and new ones are added. Old rows are never deleted.

**External APIs called**: Polygon, same as Flow 2 (no per-test calls when tests are in Redis).

**Database / storage read or written**: Reads `problems_problem`, writes `problems_problemtestcase`. Nothing in storage.

**What the user sees**: A green box: "Test cases description migrated to database Successfully." The rows can be seen in admin at `/admin/`.

---

### Flow 5: Uploading test cases to cloud storage

**What the user does**: Clicks **Migrate Test Cases to Storage**. Also disabled until Flow 3 is done.

**What the frontend sends**: `POST /` from the form `migrate-storage-form` with `problem_id` and `migrate_to_storage=1`.

**What the backend does (in order)**: `index()` first, and then `PolygonAPI.migrate_to_storage()` does the upload.
1. Before any slow call it checks the problem is in database. If not, it returns an error immediately. We need the database ID because the storage folder is named with it.
2. Runs the same fetch as Flow 2 (tests from Redis if present). The upload is kept for the end, so nothing on Polygon side can fail after files are already uploaded.
3. `get_storage()` creates the storage object for the provider we configured.
4. `delete_problem_test_case_cache()` removes Redis keys starting with `oj_dev_with_redis_storage_test_cases_{database id}`.
5. Uploads every test that has both input and output, as two files. Tests with empty input or output are skipped and noted. If not even one test was uploaded, it stops with error.
6. `upload_custom_checker()`: if problem has a custom checker, it downloads the source, compiles with `g++` and uploads the binary. If compile fails, it uploads the source as `custom_checker.cpp`.
7. Only at this point it deletes old files in the folder which were not part of this upload (`delete_problem_files()` gets the list of new files to keep).

**External APIs called**: Polygon, same as Flow 2, plus one more `problem.checker` call for every problem (to know if the checker is custom). `problem.viewFile` is called only when it is custom. And the storage provider: S3 (`put_object`, `list_objects_v2`, `delete_objects`) or Azure Blob (`upload_blob`, `list_blobs`, `delete_blob`).

**Database / storage read or written**: Reads `problems_problem` to get the ID. Writes nothing in database. Writes and deletes files under `test_cases/{database id}/` in storage.

**What the user sees**: A green box with counts, like "Uploaded 13 of 13 test cases to storage (s3) under test_cases/5/." It also tells which tests were skipped and what happened with the custom checker. On failure there is only one red box and no success message. If upload had started: "Storage migration failed: ... Files already in storage were not deleted, but files with the same name may have been replaced." If it had not started: "Storage migration did not start, so storage was not changed. Reason: ..."

---

## 2. External Integrations

### 2.1 Polygon API

Almost every call goes through `PolygonAPI._make_request()` in `problems/polygon_api.py`. The one exception is the package download (`problem.package`), which calls `requests.post` directly inside `download_and_extract_package()` with its own signature. Each call is a `POST` to `https://polygon.codeforces.com/api/{method}`.

**Endpoints used** (and which flow uses each):

| Endpoint | What we get | Used in |
|---|---|---|
| `problem.info` | Time and memory limit | Flows 2 to 5 |
| `problem.packages` | List of built packages | Flows 2 to 5 |
| `problem.package` | The package zip, `problem.html` is inside it | Flows 2 to 5 |
| `problem.tests` | Test list with index, sample flag and description | Flows 2 to 5 |
| `problem.testInput`, `problem.testAnswer` | Input and answer of one test | Flow 2, and Flows 3 to 5 if Redis is empty |
| `problem.checker` | Checker name | Flows 2 to 5 |
| `problem.updateWorkingCopy` | Nothing, it updates the working copy on Polygon | Flows 2 to 5, only if problem is in database |
| `problem.solutions`, `problem.viewSolution` | Solution list and code of the main solution | Same as above, and Flow 3 |
| `problem.viewFile` | Source of the custom checker | Flow 5 |

**How requests are authenticated**: `_generate_api_sig()` signs every request using the key and secret from `.env` (`POLYGON_API_KEY`, `POLYGON_API_SECRET`, loaded in `settings.py`).
1. Add `apiKey` and `time` (current Unix time in seconds) to the request parameters.
2. Sort all parameters by name and join them like `name=value&name=value`.
3. Make 6 random characters, call it `rand`.
4. Build the text `rand/method?parameters#secret` and hash it with SHA-512.
5. Send `apiSig = rand + hash` along with `apiKey`, `time` and the other parameters.

Polygon makes the same hash on its side and compares, so the secret itself is never sent. For JSON calls, if Polygon replies with `status: FAILED`, the code raises an error with Polygon's comment. The plain-text calls (`problem.testInput`, `problem.testAnswer`, `problem.viewSolution`, `problem.viewFile`) don't have this check, they just return the response text.

### 2.2 Cloud storage

The code is in `problems/storage/`. `base.py` has the common interface `TestCaseStorage`, and `s3.py` and `azure.py` are the two providers.

**How the provider is chosen**: `settings.py` reads `STORAGE_PROVIDER` from `.env` (default is `azure`). Then `get_storage()` in `problems/storage/__init__.py` returns `S3Storage` for `s3` and `AzureStorage` for `azure`. Any other value raises `ImproperlyConfigured`. S3 uses the `AWS_*` settings and Azure uses the `AZURE_*` ones. After changing `.env` the server has to be restarted.

**How files are named and organized**: The folder is the **database** problem ID, not the Polygon ID. `NN` is the position of the test in Polygon's list, in two digits.

```
test_cases/5/01                 input of test 1
test_cases/5/01.a               expected output of test 1
test_cases/5/02, 02.a, ...
test_cases/5/custom_checker     only for a custom checker (.exe on Windows, .cpp if it did not compile)
```

The names are built in one place only, the helper methods in `base.py`, so both providers have same layout.

**What happens on errors**: Both providers raise an error when upload, list or delete fails. S3 also checks the reply of `delete_objects`, because S3 can report failed deletes inside a normal response. If a custom checker can't be fetched or uploaded, the whole migration fails. And because old files are deleted only at the very end, a failed migration never leaves the folder empty.

### 2.3 Redis (used by Flows 2 to 5)

I added this part because Flows 2 to 5 don't make sense without it. Fetched tests are kept in Redis for 30 minutes under `polygon_migration_test_cases_{polygon id}_count` and `..._test_{n}`. Fetch always writes them. The three migrate buttons read them first and go back to Polygon only if they are missing. Any error in `index()` removes them for that problem. If Redis is down, the code just logs it and fetches from Polygon every time.
