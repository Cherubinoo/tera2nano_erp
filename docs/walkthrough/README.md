# Process walkthrough video

`t2n-erp-process-walkthrough.webm` (13 MB, about 6:39, 1280x720, no sound) follows one customer through the whole ERP using the real screens and demo data only. Captions at the bottom explain what happens, which module is used and what data moves; the chip at the top shows the chapter.

## Chapters

| # | Starts | Chapter |
|---|--------|---------|
| 1 | 0:01 | Sign in and choose a module |
| 2 | 0:21 | CRM: lead, deal and customer |
| 3 | 0:54 | Sales: quotation, approval, invoice, payment |
| 4 | 2:07 | Accounting: journal, trial balance, profit and loss |
| 5 | 2:32 | GST and Tax |
| 6 | 2:47 | Purchases: order, approval, bill, payment |
| 7 | 3:43 | Projects: tasks, time, profitability |
| 8 | 4:37 | People: employee and payroll |
| 9 | 5:03 | Partners: capital and statements |
| 10 | 5:19 | Compliance and Workplace |
| 11 | 5:56 | Reports: dashboard and export |
| 12 | 6:09 | The in-app Guide |

Timestamps come from the clock marks the script wrote while recording (`chapters.json`); they can be a second or so off.

## What is real and what is prepared

Prepared quietly through the API before recording: the company profile and GST settings, two users, one vendor, two partners, one department and one employee with a salary structure, and one extra approval rule (every purchase order needs a finance check). Everything else is typed and clicked on screen: the lead, conversion, pipeline moves, quotation, approvals, invoice, receipt, purchase order, bill and payment, project, task, timesheet, milestone invoice, new employee and payroll run, partner capital, meeting with an action item and circular. Nothing on screen is invented.

## Demo accounts (throwaway database, demo use only)

| User | Password | Role |
|------|----------|------|
| `e2eadmin` | `E2e-Test-Pass-77!` | Admin; the superuser created when the empty database is prepared |
| `meera` | `Demo-Walk-Pass-2026!` | Managing Partner (approves the quotation) |
| `farah` | `Demo-Walk-Pass-2026!` | Finance Manager (approves the purchase order) |

These exist only in the temporary database the recording uses. Never reuse these passwords anywhere real.

## How to re-record

Requirements: Node 18 or newer; the `playwright-core` package; Microsoft Edge (or set `BROWSER_EXE`); the backend virtualenv (`backend/.venv`) and `frontend/node_modules` installed; a PostgreSQL server with a **throwaway, migrated, empty database** that holds one superuser.

1. Create the database, then in `backend` run `python manage.py migrate` and `python manage.py createsuperuser --noinput --username e2eadmin --email demo@example.com` with `DJANGO_SUPERUSER_PASSWORD` set (use the same `DB_*` variables as below). Use a fresh database for every recording.
2. Run (settings are environment variables):

```
DB_NAME=t2n_walkthrough DB_HOST=127.0.0.1 DB_PORT=5432 DB_USER=postgres DB_PASSWORD=... \
PLAYWRIGHT_CORE=/path/to/node_modules/playwright-core/index.mjs \
ADMIN_USER=e2eadmin ADMIN_PASS='E2e-Test-Pass-77!' \
node record-walkthrough.mjs
```

Optional: `REPO_ROOT` (project root, default two folders above the script), `PYTHON`, `BROWSER_EXE`, `API_PORT` (8037), `WEB_PORT` (5237), `WT_OUT` (output folder, default `walkthrough-out`), `WT_PACE` (0.75; higher is slower), `WT_FAST=1` (quick dry run without video), `FFMPEG` (path to an ffmpeg that can encode VP9: shrinks the recording; the recorder's own file is large), `WT_BITRATE`.

The script starts its own backend and Vite server (and removes its temporary Vite config), records one continuous video and writes `t2n-erp-process-walkthrough.webm` and `chapters.json` (chapter times, any step that failed, page errors) into the output folder. If the screens change, adjust the matching step in the script; a step that fails never stops the recording, it shows the nearest screen and is listed in `chapters.json`.
