# Ti10 — Proctored Coding Test Platform

A Flask + Judge0 coding assessment platform with student self-registration
(admin-approved), full-screen/tab-switch enforcement with auto-submit,
per-question timers, question images, and an admin console that can
re-run a student's exact code for verification.

Tested end-to-end in this build: registration → pending → admin approval →
login, question creation with image + timer, submission grading, auto-submit
metadata, CSV export, and the admin code-review/re-run page all verified
against a live local run. (Actual Judge0 code-execution calls can't be run
from the environment this was built in due to network restrictions there —
that part was already confirmed working on your machine earlier.)

## What's new in Ti10 (vs. the earlier version)

- **Student registration + admin approval**: students sign up with name,
  email, register number, and their own password. They can't log in until
  an admin approves them from **Admin → Students**.
- **Full-screen + tab-switch enforcement**: opening a question shows a
  "Enter Full Screen & Start" gate. Exiting full screen or switching tabs
  counts as a violation; hitting the configured limit (default 3, set in
  Admin → Exam Settings) **auto-submits** the student's current code
  immediately and locks the editor.
- **Per-question timer**: set a time limit (minutes) per question in the
  admin form. Students see a circular countdown; at zero, their answer
  auto-submits.
- **Question images**: upload a diagram/image per question (like LeetCode),
  shown above the description on the student side.
- **Admin code review**: Admin → Submissions → **View Code** shows the
  student's exact submitted code, plus a **"Run this code now (verify)"**
  button that re-executes it against all test cases live, so you can
  confirm a score by eye.
- **Ti10 rebrand**: dark, glassmorphic, violet-glow visual theme; mobile
  responsive.

## 1. How it's built

```
Students → Flask app (this project) → Judge0 API → sandboxed code execution
                │
                └── SQLite (or Postgres) — students, questions, test cases,
                    submissions, drafts, settings, question attempts
```

- `app.py` — all routes (student + admin)
- `models.py` — database tables (SQLAlchemy)
- `judge0_client.py` — talks to Judge0
- `config.py` — loads `.env`, environment config
- `templates/` — pages (Bootstrap + Monaco editor via CDN, no build step)
- `static/uploads/questions/` — uploaded question images
- `static/css/style.css` — the Ti10 dark/violet theme

## 2. Run it locally

```bash
cd coding-test-platform
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env if needed — default Judge0 (ce.judge0.com) needs no key

export FLASK_APP=app.py        # Windows: set FLASK_APP=app.py
flask init-db
flask create-admin             # pick a username/password for the admin panel

python3 app.py                 # http://localhost:5000
```

Student side: `http://localhost:5000/register` then `/login` (after an
admin approves the account at `/admin/students`).
Admin side: `http://localhost:5000/admin/login`.

## 3. Judge0 options

Set in `.env`:

- **Default (free, no key)**: `JUDGE0_URL=https://ce.judge0.com` — Judge0's
  own public instance. Fine for testing / light use; can be slow or rate
  limited under many simultaneous students.
- **Self-hosted (free, unlimited, needs a server with Docker)** — recommended
  for a real exam:
  ```bash
  git clone https://github.com/judge0/judge0.git
  cd judge0 && docker compose up -d
  ```
  Then `JUDGE0_URL=http://<your-server>:2358` (leave `RAPIDAPI_KEY` blank).
- **RapidAPI Judge0 CE**: now pay-per-use, requires a card on file. Only use
  if you specifically want this — see `.env.example` for the settings.

## 4. Deploy for free (Render)

1. Push this project to a GitHub repo.
2. Render → New → Web Service → connect the repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Instance type: Free
3. Add environment variables from `.env.example` (`SECRET_KEY`, `JUDGE0_URL`,
   `RAPIDAPI_KEY`, `RAPIDAPI_HOST`).
4. Add Render's free Postgres and set `DATABASE_URL` to its Internal
   Database URL (SQLite on Render's free tier isn't guaranteed to survive
   redeploys — student accounts and submissions could vanish otherwise).
5. After first deploy, open the **Shell** tab and run once:
   ```bash
   flask init-db
   flask create-admin
   ```
6. Share the Render URL with students; you log in at `/admin/login` on the
   same URL.

Note: Render's free tier sleeps after ~15 min idle and takes 30–60s to wake
on the next visit — expected on first load, not a bug.

## 5. Using it for an exam

**Admin:**
1. **Exam Settings** — set title, optional start/end window, and max tab
   switches (default 3) before opening registration to students.
2. **Students** — approve pending sign-ups (or reject spam/duplicates).
3. **+ New Question** — title, description, optional image, marks, optional
   per-question timer, allowed languages, and test cases (mark some Hidden).
4. **Submissions** — monitor live, **View Code** to inspect and re-run any
   student's answer, **Export CSV** for records/marks entry.

**Student:**
1. Register (name, email, register number, password) → wait for approval.
2. Log in, open a question, click **Enter Full Screen & Start**.
3. Write code, **Run** against sample cases, **Submit** for final grading.
4. Leaving full screen or switching tabs beyond the limit, or the timer
   hitting zero, auto-submits their current code and locks the editor.

## 6. What's intentionally out of scope for this build

Live leaderboard and PDF reports aren't built here. The database structure
makes both straightforward to add later — ask if you want them.

## 7. Notes on the anti-cheating measures

Full-screen + tab-switch detection use standard browser APIs
(`Fullscreen API`, `visibilitychange`). No enforcement of this kind is
unbeatable (e.g. dev tools, virtual machines, phones as a second device
can't be blocked from a web page) — treat these as a deterrent and
signal for review (the tab-switch count and auto-submit reason are visible
per submission in the admin console), not an absolute guarantee.

## 8. Language IDs / limits

Python 3 = 71, Java = 62, C++ = 54, C = 50, JavaScript (Node) = 63 — edit
`LANGUAGES` in `judge0_client.py` to add more; the question form's language
checkboxes update automatically.
