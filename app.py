import csv
import io
import os
import sys
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    jsonify, flash, Response
)

from config import Config
from models import (
    db, Admin, Student, Settings, Question, TestCase,
    Submission, Draft, QuestionAttempt, utcnow
)
import judge0_client as judge0
from datetime import timedelta

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)


def ensure_db_ready():
    """Idempotent: creates any missing tables/rows. Safe to call repeatedly."""
    db.create_all()
    if Settings.query.first() is None:
        db.session.add(Settings(exam_title="Ti10", max_tab_switches=3))
        db.session.commit()
    if Admin.query.first() is None:
        admin = Admin(username="KITCSE")
        admin.set_password("CSE1234")
        db.session.add(admin)
        db.session.commit()


with app.app_context():
    ensure_db_ready()


@app.before_request
def _check_db():
    from sqlalchemy import inspect
    try:
        if "settings" not in inspect(db.engine).get_table_names():
            with app.app_context():
                ensure_db_ready()
    except Exception:
        with app.app_context():
            ensure_db_ready()


LANGUAGES = judge0.LANGUAGES


@app.template_filter("ist")
def to_ist(dt):
    """Converts a naive UTC datetime to IST for display."""
    if not dt:
        return ""
    ist_dt = dt + timedelta(hours=5, minutes=30)
    return ist_dt.strftime("%d %b %Y, %I:%M %p")

UPLOAD_DIR = os.path.join(app.static_folder, "uploads", "questions")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}


# --------------------------------------------------------------------------
# Auth helpers
# --------------------------------------------------------------------------
def student_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("student_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


# --------------------------------------------------------------------------
# Student auth: register / login (with admin approval gate)
# --------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    if session.get("student_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        register_no = request.form.get("register_no", "").strip()
        year = request.form.get("year", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not name or not email or not register_no or not password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("register"))
        if password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(url_for("register"))

        if Student.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "error")
            return redirect(url_for("register"))
        if Student.query.filter_by(register_no=register_no).first():
            flash("An account with this register number already exists.", "error")
            return redirect(url_for("register"))

        student = Student(name=name, email=email, register_no=register_no, year=year or None, status="pending")
        student.set_password(password)
        db.session.add(student)
        db.session.commit()

        flash("Registered! Your account is pending admin approval before you can log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", settings=Settings.get())


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        student = Student.query.filter_by(email=email).first()
        if not student or not student.check_password(password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        if student.status == "pending":
            flash("Your account is still pending admin approval.", "error")
            return redirect(url_for("login"))
        if student.status == "rejected":
            flash("Your registration was not approved. Contact your administrator.", "error")
            return redirect(url_for("login"))

        session.clear()
        session["student_id"] = student.id
        session["student_name"] = student.name
        return redirect(url_for("dashboard"))

    return render_template("login.html", settings=Settings.get())


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Student dashboard / question / run / submit
# --------------------------------------------------------------------------
@app.route("/dashboard")
@student_required
def dashboard():
    settings = Settings.get()
    student = Student.query.get(session["student_id"])
    all_questions = Question.query.filter_by(is_published=True, is_released=True).order_by(Question.id).all()
    questions = [q for q in all_questions if q.visible_to_year(student.year)]

    best_scores = {}
    subs = Submission.query.filter_by(student_id=session["student_id"]).all()
    for s in subs:
        cur = best_scores.get(s.question_id)
        if cur is None or s.score > cur:
            best_scores[s.question_id] = s.score

    return render_template(
        "dashboard.html",
        questions=questions,
        best_scores=best_scores,
        settings=settings,
        is_open=settings.is_open(),
        student=student,
    )


def _question_deadline_seconds(q, student_id):
    """Returns remaining seconds for a per-question timer, or None if untimed."""
    if not q.question_time_limit_min:
        return None
    attempt = QuestionAttempt.query.filter_by(student_id=student_id, question_id=q.id).first()
    if not attempt:
        attempt = QuestionAttempt(student_id=student_id, question_id=q.id)
        db.session.add(attempt)
        db.session.commit()
    elapsed = (utcnow() - attempt.started_at).total_seconds()
    remaining = q.question_time_limit_min * 60 - elapsed
    return max(0, int(remaining))


def _check_question_access(q, student):
    """Returns True if this student is currently allowed to see/attempt this question."""
    if not q.is_released:
        return False
    if not q.visible_to_year(student.year):
        return False
    return True


@app.route("/question/<int:qid>")
@student_required
def question_page(qid):
    q = Question.query.get_or_404(qid)
    student = Student.query.get(session["student_id"])
    if not _check_question_access(q, student):
        flash("This question isn't available to you yet.", "error")
        return redirect(url_for("dashboard"))

    settings = Settings.get()
    draft = Draft.query.filter_by(student_id=session["student_id"], question_id=qid).first()
    allowed = q.allowed_language_ids()
    lang_options = [(lid, LANGUAGES.get(lid, f"Lang {lid}")) for lid in allowed]

    remaining_seconds = _question_deadline_seconds(q, session["student_id"])

    return render_template(
        "question.html",
        q=q,
        draft=draft,
        lang_options=lang_options,
        settings=settings,
        is_open=settings.is_open(),
        remaining_seconds=remaining_seconds,
    )


@app.route("/draft/<int:qid>", methods=["POST"])
@student_required
def save_draft(qid):
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    language_id = int(data.get("language_id", 71))

    draft = Draft.query.filter_by(student_id=session["student_id"], question_id=qid).first()
    if not draft:
        draft = Draft(student_id=session["student_id"], question_id=qid)
        db.session.add(draft)
    draft.code = code
    draft.language_id = language_id
    draft.updated_at = utcnow()
    db.session.commit()
    return jsonify({"ok": True, "saved_at": draft.updated_at.isoformat()})


def _run_test_cases(code, language_id, test_cases, cpu_time_limit, memory_limit):
    results = []
    for tc in test_cases:
        r = judge0.run_code(
            source_code=code,
            language_id=language_id,
            stdin=tc.input,
            expected_output=tc.expected_output,
            cpu_time_limit=cpu_time_limit,
            memory_limit=memory_limit,
        )
        if "error" in r:
            results.append({"passed": False, "status": "Error", "detail": r["error"], "tc": tc})
            continue

        status_desc = (r.get("status") or {}).get("description", "Unknown")
        passed = status_desc == "Accepted"
        results.append({
            "passed": passed,
            "status": status_desc,
            "stdout": r.get("stdout"),
            "stderr": r.get("stderr"),
            "compile_output": r.get("compile_output"),
            "time": r.get("time"),
            "memory": r.get("memory"),
            "tc": tc,
        })
    return results


@app.route("/run/<int:qid>", methods=["POST"])
@student_required
def run_code_route(qid):
    q = Question.query.get_or_404(qid)
    student = Student.query.get(session["student_id"])
    if not _check_question_access(q, student):
        return jsonify({"error": "This question isn't available to you."}), 403

    settings = Settings.get()
    if not settings.is_open():
        return jsonify({"error": "The test window is closed."}), 403

    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    language_id = int(data.get("language_id", 71))

    if language_id not in q.allowed_language_ids():
        return jsonify({"error": "Language not allowed for this question."}), 400
    if not code.strip():
        return jsonify({"error": "Please write some code first."}), 400

    public_cases = q.public_test_cases()
    if not public_cases:
        return jsonify({"message": "No public test cases for this question. Try Submit."})

    results = _run_test_cases(code, language_id, public_cases, q.time_limit_sec, q.memory_limit_kb)

    out = []
    for res in results:
        out.append({
            "input": res["tc"].input,
            "expected_output": res["tc"].expected_output,
            "actual_output": (res.get("stdout") or "").strip(),
            "status": res["status"],
            "passed": res["passed"],
            "stderr": res.get("stderr") or res.get("detail"),
            "compile_output": res.get("compile_output"),
        })
    return jsonify({"results": out})


@app.route("/submit/<int:qid>", methods=["POST"])
@student_required
def submit_code(qid):
    q = Question.query.get_or_404(qid)
    student = Student.query.get(session["student_id"])
    if not _check_question_access(q, student):
        return jsonify({"error": "This question isn't available to you."}), 403

    settings = Settings.get()
    if not settings.is_open():
        return jsonify({"error": "The test window is closed."}), 403

    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    language_id = int(data.get("language_id", 71))
    tab_switches = int(data.get("tab_switches", 0))
    auto_submitted = bool(data.get("auto_submitted", False))
    auto_submit_reason = data.get("auto_submit_reason")

    if language_id not in q.allowed_language_ids():
        return jsonify({"error": "Language not allowed for this question."}), 400
    if not code.strip() and not auto_submitted:
        return jsonify({"error": "Please write some code first."}), 400

    all_cases = q.test_cases
    if not all_cases:
        return jsonify({"error": "This question has no test cases configured yet."}), 400

    results = _run_test_cases(code, language_id, all_cases, q.time_limit_sec, q.memory_limit_kb)
    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)
    score = round(q.marks * passed_count / total_count, 2) if total_count else 0

    submission = Submission(
        student_id=session["student_id"],
        question_id=qid,
        code=code,
        language_id=language_id,
        score=score,
        max_score=q.marks,
        passed_count=passed_count,
        total_count=total_count,
        status="Evaluated",
        tab_switches=tab_switches,
        auto_submitted=auto_submitted,
        auto_submit_reason=auto_submit_reason,
    )
    db.session.add(submission)

    draft = Draft.query.filter_by(student_id=session["student_id"], question_id=qid).first()
    if draft:
        db.session.delete(draft)
    db.session.commit()

    public_results = []
    for r in results:
        if not r["tc"].is_hidden:
            public_results.append({
                "input": r["tc"].input,
                "expected_output": r["tc"].expected_output,
                "actual_output": (r.get("stdout") or "").strip(),
                "status": r["status"],
                "passed": r["passed"],
            })

    return jsonify({
        "score": score,
        "max_score": q.marks,
        "passed_count": passed_count,
        "total_count": total_count,
        "public_results": public_results,
    })


# --------------------------------------------------------------------------
# Admin auth
# --------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            session.clear()
            session["admin_id"] = admin.id
            session["admin_username"] = admin.username
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# --------------------------------------------------------------------------
# Admin: student approvals
# --------------------------------------------------------------------------
@app.route("/admin/students")
@admin_required
def admin_students():
    status_filter = request.args.get("status", "pending")
    query = Student.query
    if status_filter in ("pending", "approved", "rejected"):
        query = query.filter_by(status=status_filter)
    students = query.order_by(Student.created_at.desc()).all()
    pending_count = Student.query.filter_by(status="pending").count()
    years = sorted(set(s.year for s in Student.query.all() if s.year))
    return render_template(
        "admin_students.html",
        students=students,
        status_filter=status_filter,
        pending_count=pending_count,
        years=years,
    )


@app.route("/admin/students/<int:sid>/approve", methods=["POST"])
@admin_required
def admin_approve_student(sid):
    s = Student.query.get_or_404(sid)
    s.status = "approved"
    year = request.form.get("year", "").strip()
    if year:
        s.year = year
    db.session.commit()
    flash(f"{s.name} approved.", "success")
    return redirect(url_for("admin_students", status="pending"))


@app.route("/admin/students/<int:sid>/reject", methods=["POST"])
@admin_required
def admin_reject_student(sid):
    s = Student.query.get_or_404(sid)
    s.status = "rejected"
    db.session.commit()
    flash(f"{s.name} rejected.", "success")
    return redirect(url_for("admin_students", status="pending"))


@app.route("/admin/students/<int:sid>/set-year", methods=["POST"])
@admin_required
def admin_set_student_year(sid):
    s = Student.query.get_or_404(sid)
    s.year = request.form.get("year", "").strip() or None
    db.session.commit()
    flash(f"Year updated for {s.name}.", "success")
    return redirect(url_for("admin_students", status=request.form.get("status_filter", "approved")))


@app.route("/admin/students/clear-data", methods=["POST"])
@admin_required
def admin_clear_student_data():
    year = request.form.get("year", "").strip()
    q = Student.query
    if year:
        q = q.filter_by(year=year)
    student_ids = [s.id for s in q.all()]
    if not student_ids:
        flash("No students found for that year.", "error")
        return redirect(url_for("admin_students", status="approved"))

    Submission.query.filter(Submission.student_id.in_(student_ids)).delete(synchronize_session=False)
    Draft.query.filter(Draft.student_id.in_(student_ids)).delete(synchronize_session=False)
    QuestionAttempt.query.filter(QuestionAttempt.student_id.in_(student_ids)).delete(synchronize_session=False)
    db.session.commit()
    flash(
        f"Cleared submissions/drafts for {len(student_ids)} student(s)"
        f"{' in year ' + year if year else ''}. Accounts kept.",
        "success",
    )
    return redirect(url_for("admin_students", status="approved"))


# --------------------------------------------------------------------------
# Admin dashboard / questions
# --------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    year_filter = request.args.get("year", "").strip()
    q = Question.query
    if year_filter:
        q = q.filter_by(year=year_filter)
    questions = q.order_by(Question.id.desc()).all()
    students_count = Student.query.filter_by(status="approved").count()
    pending_count = Student.query.filter_by(status="pending").count()
    submissions_count = Submission.query.count()
    settings = Settings.get()
    years = sorted(set(x.year for x in Question.query.all() if x.year))
return render_template(
        "admin_dashboard.html",
        questions=questions,
        students_count=students_count,
        pending_count=pending_count,
        submissions_count=submissions_count,
        settings=settings,
        years=years,
        year_filter=year_filter,
        LANGUAGES=LANGUAGES,
    )


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_update_settings():
    settings = Settings.get()
    settings.exam_title = request.form.get("exam_title", "Ti10").strip()

    start = request.form.get("start_time", "").strip()
    end = request.form.get("end_time", "").strip()
    settings.start_time = datetime.fromisoformat(start) if start else None
    settings.end_time = datetime.fromisoformat(end) if end else None
    settings.max_tab_switches = int(request.form.get("max_tab_switches", 3))

    db.session.commit()
    flash("Settings updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/question/new", methods=["GET", "POST"])
@admin_required
def admin_new_question():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        marks = int(request.form.get("marks", 10))
        time_limit_sec = float(request.form.get("time_limit_sec", 2.0))
        memory_limit_kb = int(request.form.get("memory_limit_kb", 128000))
        languages = request.form.getlist("languages")
        is_published = bool(request.form.get("is_published"))
        q_time_limit = request.form.get("question_time_limit_min", "").strip()
        year = request.form.get("year", "").strip() or None

        if not title or not description or not languages:
            flash("Title, description and at least one language are required.", "error")
            return redirect(url_for("admin_new_question"))

        image_filename = None
        image_file = request.files.get("image")
        if image_file and image_file.filename and allowed_image(image_file.filename):
            ext = image_file.filename.rsplit(".", 1)[1].lower()
            image_filename = f"{uuid.uuid4().hex}.{ext}"
            image_file.save(os.path.join(UPLOAD_DIR, image_filename))

        q = Question(
            title=title,
            description=description,
            image_filename=image_filename,
            marks=marks,
            time_limit_sec=time_limit_sec,
            memory_limit_kb=memory_limit_kb,
            question_time_limit_min=int(q_time_limit) if q_time_limit else None,
            allowed_languages=",".join(languages),
            is_published=is_published,
            year=year,
        )
        db.session.add(q)
        db.session.flush()

        tc_inputs = request.form.getlist("tc_input[]")
        tc_outputs = request.form.getlist("tc_output[]")
        tc_types = request.form.getlist("tc_type[]")

        for i in range(len(tc_inputs)):
            db.session.add(TestCase(
                question_id=q.id,
                input=tc_inputs[i],
                expected_output=tc_outputs[i],
                is_hidden=(tc_types[i] == "hidden") if i < len(tc_types) else False,
            ))

        db.session.commit()
        flash("Question created. Use 'Release Now' on the dashboard when you're ready for students to see it.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_question_form.html", languages=LANGUAGES, question=None)


@app.route("/admin/question/<int:qid>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_question(qid):
    q = Question.query.get_or_404(qid)

    if request.method == "POST":
        q.title = request.form.get("title", "").strip()
        q.description = request.form.get("description", "").strip()
        q.marks = int(request.form.get("marks", 10))
        q.time_limit_sec = float(request.form.get("time_limit_sec", 2.0))
        q.memory_limit_kb = int(request.form.get("memory_limit_kb", 128000))
        q.allowed_languages = ",".join(request.form.getlist("languages"))
        q.is_published = bool(request.form.get("is_published"))
        q.year = request.form.get("year", "").strip() or None
        q_time_limit = request.form.get("question_time_limit_min", "").strip()
        q.question_time_limit_min = int(q_time_limit) if q_time_limit else None

        remove_image = request.form.get("remove_image")
        image_file = request.files.get("image")
        if remove_image:
            q.image_filename = None
        if image_file and image_file.filename and allowed_image(image_file.filename):
            ext = image_file.filename.rsplit(".", 1)[1].lower()
            image_filename = f"{uuid.uuid4().hex}.{ext}"
            image_file.save(os.path.join(UPLOAD_DIR, image_filename))
            q.image_filename = image_filename

        TestCase.query.filter_by(question_id=q.id).delete()
        tc_inputs = request.form.getlist("tc_input[]")
        tc_outputs = request.form.getlist("tc_output[]")
        tc_types = request.form.getlist("tc_type[]")
        for i in range(len(tc_inputs)):
            db.session.add(TestCase(
                question_id=q.id,
                input=tc_inputs[i],
                expected_output=tc_outputs[i],
                is_hidden=(tc_types[i] == "hidden") if i < len(tc_types) else False,
            ))

        db.session.commit()
        flash("Question updated.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_question_form.html", languages=LANGUAGES, question=q)


@app.route("/admin/question/<int:qid>/delete", methods=["POST"])
@admin_required
def admin_delete_question(qid):
    q = Question.query.get_or_404(qid)
    Submission.query.filter_by(question_id=qid).delete()
    QuestionAttempt.query.filter_by(question_id=qid).delete()
    db.session.delete(q)
    db.session.commit()
    flash("Question deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/question/<int:qid>/release", methods=["POST"])
@admin_required
def admin_release_question(qid):
    q = Question.query.get_or_404(qid)
    q.is_released = True
    q.released_at = utcnow()
    db.session.commit()
    flash(f'"{q.title}" released to students.', "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/question/<int:qid>/unrelease", methods=["POST"])
@admin_required
def admin_unrelease_question(qid):
    q = Question.query.get_or_404(qid)
    q.is_released = False
    db.session.commit()
    flash(f'"{q.title}" hidden from students.', "success")
    return redirect(url_for("admin_dashboard"))


# --------------------------------------------------------------------------
# Admin submissions / review / re-run / export
# --------------------------------------------------------------------------
@app.route("/admin/submissions")
@admin_required
def admin_submissions():
    qid = request.args.get("question_id", type=int)
    query = Submission.query
    if qid:
        query = query.filter_by(question_id=qid)
    submissions = query.order_by(Submission.submitted_at.desc()).all()
    questions = Question.query.order_by(Question.id).all()
    return render_template(
        "admin_submissions.html",
        submissions=submissions,
        questions=questions,
        selected_qid=qid,
        LANGUAGES=LANGUAGES,
    )


@app.route("/admin/submission/<int:sub_id>")
@admin_required
def admin_view_submission(sub_id):
    sub = Submission.query.get_or_404(sub_id)
    return render_template("admin_submission_detail.html", sub=sub, LANGUAGES=LANGUAGES)


@app.route("/admin/submission/<int:sub_id>/rerun", methods=["POST"])
@admin_required
def admin_rerun_submission(sub_id):
    sub = Submission.query.get_or_404(sub_id)
    q = sub.question
    results = _run_test_cases(sub.code, sub.language_id, q.test_cases, q.time_limit_sec, q.memory_limit_kb)
    out = []
    for r in results:
        out.append({
            "input": r["tc"].input,
            "expected_output": r["tc"].expected_output,
            "actual_output": (r.get("stdout") or "").strip(),
            "status": r["status"],
            "passed": r["passed"],
            "is_hidden": r["tc"].is_hidden,
            "stderr": r.get("stderr") or r.get("detail"),
            "compile_output": r.get("compile_output"),
        })
    passed = sum(1 for r in out if r["passed"])
    return jsonify({"results": out, "passed_count": passed, "total_count": len(out)})


@app.route("/admin/export.csv")
@admin_required
def admin_export_csv():
    qid = request.args.get("question_id", type=int)
    query = Submission.query
    if qid:
        query = query.filter_by(question_id=qid)
    submissions = query.order_by(Submission.submitted_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Student Name", "Register No", "Year", "Question", "Language",
        "Score", "Max Score", "Passed", "Total", "Tab Switches",
        "Auto-Submitted", "Reason", "Submitted At (IST)"
    ])
    for s in submissions:
        try:
            writer.writerow([
                s.student.name if s.student else "",
                s.student.register_no if s.student else "",
                s.student.year if s.student and s.student.year else "",
                s.question.title if s.question else "",
                LANGUAGES.get(s.language_id, s.language_id),
                s.score if s.score is not None else 0,
                s.max_score if s.max_score is not None else 0,
                s.passed_count if s.passed_count is not None else 0,
                s.total_count if s.total_count is not None else 0,
                s.tab_switches if s.tab_switches is not None else 0,
                "Yes" if s.auto_submitted else "No",
                s.auto_submit_reason or "",
                to_ist(s.submitted_at) if s.submitted_at else "",
            ])
        except Exception:
            # Skip a malformed row rather than crashing the whole report
            continue

    output = buf.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=ti10_submissions_report.csv"},
    )


# --------------------------------------------------------------------------
# CLI commands
# --------------------------------------------------------------------------
@app.cli.command("init-db")
def init_db():
    """Create all database tables."""
    with app.app_context():
        db.create_all()
        Settings.get()
    print("Database initialized.")


@app.cli.command("create-admin")
def create_admin():
    """Create an admin user interactively."""
    import getpass
    username = input("Admin username: ").strip()
    password = getpass.getpass("Admin password: ").strip()
    with app.app_context():
        if Admin.query.filter_by(username=username).first():
            print("Admin already exists.")
            return
        admin = Admin(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
    print(f"Admin '{username}' created.")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        Settings.get()
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode, use_reloader=False)