# Polygon Migration Tool

A Django web app for staff users. It copies a competitive-programming problem from
[Polygon](https://polygon.codeforces.com/) into this project's PostgreSQL database and
uploads the problem's test-case files to cloud storage (AWS S3 or Azure Blob Storage).

Related documents in this repository:

- `DOCUMENTATION.md` explains each user flow from the browser to the database and storage.
- `ISSUES.md` lists known product and code issues and the edge-case analysis.

## What the tool does

1. A staff user logs in and enters a Polygon problem ID.
2. The app calls the Polygon API, downloads the problem package and shows the statement,
   the test cases and the main solution.
3. The user picks a difficulty and tags and saves the problem to the database.
4. The user saves the test cases to the database (shortened copies, for display).
5. The user uploads the full test-case files to cloud storage.

```
Browser -> Django view (problems/views.py) -> Polygon API   (problem data, tests)
                                           -> PostgreSQL    (problem, tags, test rows)
                                           -> Redis         (30-minute cache of fetched tests)
                                           -> S3 or Azure   (test-case files)
```

## Tech stack

| Part | Used for |
|---|---|
| Python 3.10+ and Django 5.2 | Web app (Django 5.2 does not run on older Python) |
| PostgreSQL | Users, problems, tags, test cases. SQLite is not supported. |
| Redis | Caches the test cases fetched from Polygon for 30 minutes |
| boto3 | AWS S3 storage |
| azure-storage-blob, azure-identity | Azure Blob storage |
| g++ (optional) | Compiles a problem's custom checker before upload |

## Project structure

```
README.md, DOCUMENTATION.md, ISSUES.md
requirement.txt                  Python dependencies (note: singular file name)
PolygonMigration/
  manage.py
  .env.example                   Template for your .env (copy it next to manage.py)
  PolygonMigration/
    settings.py                  Reads .env; database, Redis, Polygon and storage settings
    urls.py                      /admin/, / (problems app), /users/
  problems/                      The migration tool
    views.py                     index(): the single page; every button posts to it
    polygon_api.py               PolygonAPI: signed Polygon requests, Redis cache, storage migration
    models.py                    Problem, ProblemTag, SampleTestCase, ProblemTestCase
    storage/
      base.py                    TestCaseStorage: the storage interface and file naming
      s3.py                      S3Storage
      azure.py                   AzureStorage
      __init__.py                get_storage(): picks the provider from STORAGE_PROVIDER
    AzureTestcase.py             AzureBlobManager from the starter code (used for Azure login)
    templates/problems/index.html  The page and its JavaScript
    tests.py                     Tests for the storage layer and storage migration
    admin.py, urls.py, migrations/
  users/                         Custom User model (email login), EmailBackend, login and logout views
  contents/                      Topic model only; not part of the migration flow
```

## Setup

### 1. Prerequisites

- Python 3.10 or newer
- Docker (the easiest way to run PostgreSQL and Redis), or local installs of both
- A Polygon account with an API key, and a problem that has a built **Standard package**
- An AWS account with an S3 bucket (or an Azure storage account)

### 2. Get the code and install dependencies

```bash
git clone https://github.com/manoj8374/polygon-migration-assignment.git
cd polygon-migration-assignment/PolygonMigration
python -m venv venv
venv\Scripts\activate          # Windows.  macOS/Linux: source venv/bin/activate
pip install -r ../requirement.txt
```

### 3. Start PostgreSQL and Redis

```bash
docker run --name polygon-postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=polygon_migration -p 5432:5432 -d postgres:15
docker run --name polygon-redis -p 6379:6379 -d redis:7
```

On later days, start the same containers again with `docker start polygon-postgres polygon-redis`.

### 4. Create your `.env`

Copy `PolygonMigration/.env.example` to `PolygonMigration/.env` (next to `manage.py`) and fill it in.
Never commit `.env`; it is listed in `.gitignore`.

| Variable | Meaning |
|---|---|
| `SECRET_KEY` | Django secret key (any long random string) |
| `DEBUG` | `True` for local development |
| `ALLOWED_HOSTS` | Comma-separated hosts, for example `localhost,127.0.0.1` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection. The defaults in `.env.example` match the Docker command above. |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_SSL` | Redis connection |
| `POLYGON_API_KEY`, `POLYGON_API_SECRET` | From Polygon: Settings, then the API section. The app refuses to start without them. |
| `STORAGE_PROVIDER` | `s3` or `azure`. Other providers are not supported. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_S3_BUCKET_NAME` | Needed when `STORAGE_PROVIDER=s3` |
| `AWS_S3_ENDPOINT_URL` | Leave empty for AWS |
| `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_USERNAME`, `AZURE_PASSWORD`, `AZURE_CONTAINER_NAME` | Needed when `STORAGE_PROVIDER=azure` |
| `CUSTOM_CHECKER_DIR` | Optional folder used to compile custom checkers. A temporary folder is used if empty. |

### 5. Set up S3 (when `STORAGE_PROVIDER=s3`)

1. Create a bucket in the S3 console. Keep "Block all public access" turned on. Note its region.
2. Create an IAM user for the app with this policy (replace `YOUR-BUCKET`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::YOUR-BUCKET" },
    { "Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"], "Resource": "arn:aws:s3:::YOUR-BUCKET/*" }
  ]
}
```

3. Create an access key for that user and put it in `.env`:

```env
STORAGE_PROVIDER=s3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-south-1
AWS_S3_BUCKET_NAME=YOUR-BUCKET
```

For Azure, set `STORAGE_PROVIDER=azure` and the six `AZURE_*` variables instead. The Azure
provider logs in with a username and password, which does not work for accounts that use MFA.
It has unit tests but has not been run against a real Azure account.

### 6. Create the database tables and a staff user

```bash
python manage.py migrate
python manage.py createsuperuser
```

`createsuperuser` asks for email, names, username, contact number, college, graduation year and
gender (`MALE`, `FEMALE` or `OTHER`, in capitals). You log in with the **email**.

### 7. Run

```bash
python manage.py runserver
```

Open http://localhost:8000/. You are sent to `/users/login/`.

## Using the app

| Step | Button | What it does |
|---|---|---|
| 1 | **Fetch Problem** | Fetches the problem from Polygon and shows it. Nothing is saved. |
| 2 | **Create/Update problem in Database** | Saves the problem, difficulty, tags and sample tests. Needs a difficulty. |
| 3 | **Migrate Test Description to Database** | Saves every test case to the database, with input and output cut to 260 characters. |
| 4 | **Migrate Test Cases to Storage** | Uploads the full test files to the configured storage provider. |

Steps 3 and 4 are disabled until step 2 has been done for that problem. Every button first
fetches the problem again from Polygon, so each click can take a while.

The Django admin at `/admin/` shows the saved `Problem`, `ProblemTag`, `SampleTestCase` and
`ProblemTestCase` rows.

## Storage layout and how to check an upload

Files are named with the **database** problem ID (shown on the page after step 2), not the Polygon ID:

```
test_cases/{database problem id}/{NN}      input file   (NN = 01, 02, ... in Polygon test order)
test_cases/{database problem id}/{NN}.a    expected output
test_cases/{database problem id}/custom_checker[.exe | .cpp]   only for problems with a custom checker
```

To check an upload:

1. The page shows a message such as `Uploaded 13 of 13 test cases to storage (s3) under test_cases/5/.`
2. In the S3 console open the bucket, then `test_cases/`, then the problem ID folder. There should be
   two objects per test.
3. Open one pair, for example `01` and `01.a`, and compare them with the test on Polygon.

With the AWS CLI: `aws s3 ls s3://YOUR-BUCKET/test_cases/5/`

How a storage migration behaves:

- New files are uploaded first. Files left from an earlier migration are deleted only after every
  upload succeeded, so a failed migration never leaves the folder empty.
- If anything fails, the page shows one error message and no success message.
- Tests with an empty input or output are skipped and listed in the message.
- If a custom checker cannot be fetched or uploaded, the migration fails. If it only fails to
  compile (for example `g++` is not installed), its source is uploaded as `custom_checker.cpp`
  and the message says so.

## Adding another storage provider

1. Add a class in `problems/storage/` that extends `TestCaseStorage` and implements
   `upload_test_case`, `upload_file` and `delete_problem_files`. Use the naming helpers in
   `base.py` so the layout stays the same, and raise an exception on any failure.
2. Add its settings to `settings.py` and `.env.example`.
3. Add a branch for it in `get_storage()` in `problems/storage/__init__.py`.

The view and `PolygonAPI.migrate_to_storage` do not need to change.

## Tests

```bash
python manage.py test problems
```

The tests use fakes and mocks. They need no database, Polygon access or cloud account, but
`.env` must contain the Polygon key variables because `settings.py` checks them at start-up.

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| `POLYGON_API_KEY and POLYGON_API_SECRET must be set` at start-up | `.env` is missing, in the wrong folder, or lacks the keys. It must sit next to `manage.py`. |
| A change in `.env` has no effect | Stop the server and start it again. The auto-reloader keeps the old environment values. |
| `column ... does not exist` | Run `python manage.py migrate`. |
| Fetch fails with "No packages available" | Build a Standard package for the problem on Polygon, after committing your changes there. |
| Cannot log in | Use the email, not the username. The user must have staff access. |
| "CSRF verification failed" on login | Reload the login page and use one tab. Do not mix `localhost` and `127.0.0.1`. |
| Storage upload fails with an access error | Check the bucket name, region and the IAM policy in step 5. |
| Database connection refused | The PostgreSQL container is stopped: `docker start polygon-postgres polygon-redis`. |
