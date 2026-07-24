from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def utcnow():
    """Naive UTC datetime (matches existing naive DateTime columns/comparisons),
    without using the deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Admin(db.Model):
    __tablename__ = "admins"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Student(db.Model):
    __tablename__ = "students"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    register_no = db.Column(db.String(60), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending | approved | rejected
    year = db.Column(db.String(20), nullable=True)  # e.g. "2027" — set by admin
    created_at = db.Column(db.DateTime, default=utcnow)
    is_club_member = db.Column(db.Boolean, default=False)

    submissions = db.relationship("Submission", backref="student", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_approved(self):
        return self.status == "approved"


class Settings(db.Model):
    """Single-row table holding exam window / branding."""
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    exam_title = db.Column(db.String(200), default="Ti10")
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    max_tab_switches = db.Column(db.Integer, default=3)
    allow_guest_login = db.Column(db.Boolean, default=False)
    published_leaderboard_years = db.Column(db.Text, nullable=True)  # comma-separated year list

    def is_leaderboard_published(self, year):
        if not self.published_leaderboard_years:
            return False
        published = [y.strip() for y in self.published_leaderboard_years.split(",") if y.strip()]
        return (year or "") in published

    def publish_year(self, year):
        current = [y.strip() for y in (self.published_leaderboard_years or "").split(",") if y.strip()]
        if year not in current:
            current.append(year)
        self.published_leaderboard_years = ",".join(current)

    def unpublish_year(self, year):
        current = [y.strip() for y in (self.published_leaderboard_years or "").split(",") if y.strip()]
        current = [y for y in current if y != year]
        self.published_leaderboard_years = ",".join(current)

    @staticmethod
    def get():
        s = Settings.query.first()
        if not s:
            s = Settings(exam_title="Ti10", max_tab_switches=3)
            db.session.add(s)
            db.session.commit()
        return s

    def is_open(self):
        now = utcnow()
        if self.start_time and now < self.start_time:
            return False
        if self.end_time and now > self.end_time:
            return False
        return True


class Question(db.Model):
    __tablename__ = "questions"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    marks = db.Column(db.Integer, default=10)
    time_limit_sec = db.Column(db.Float, default=2.0)          # Judge0 CPU time limit per run
    memory_limit_kb = db.Column(db.Integer, default=128000)
    question_time_limit_min = db.Column(db.Integer, nullable=True)  # None = no per-question timer
    allowed_languages = db.Column(db.String(100), default="71,62,54,50")
    is_published = db.Column(db.Boolean, default=True)
    year = db.Column(db.String(20), nullable=True)          # None/blank = visible to all years
    is_released = db.Column(db.Boolean, default=False)      # admin must manually release
    released_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    mode = db.Column(db.String(20), default="exam")  # exam | club | hackathon

    test_cases = db.relationship(
        "TestCase", backref="question", lazy=True, cascade="all, delete-orphan"
    )
    submissions = db.relationship("Submission", backref="question", lazy=True)

    def allowed_language_ids(self):
        return [int(x) for x in self.allowed_languages.split(",") if x.strip()]

    def public_test_cases(self):
        return [tc for tc in self.test_cases if not tc.is_hidden]

    def hidden_test_cases(self):
        return [tc for tc in self.test_cases if tc.is_hidden]

    def visible_to_year(self, year):
        return not self.year or not year or self.year == year


class TestCase(db.Model):
    __tablename__ = "test_cases"
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    input = db.Column(db.Text, default="")
    expected_output = db.Column(db.Text, default="")
    is_hidden = db.Column(db.Boolean, default=False)


class QuestionAttempt(db.Model):
    """Tracks when a student first opened a timed question, to compute their countdown."""
    __tablename__ = "question_attempts"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    started_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (db.UniqueConstraint("student_id", "question_id", name="uq_attempt_student_question"),)


class Submission(db.Model):
    __tablename__ = "submissions"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    code = db.Column(db.Text, nullable=False)
    language_id = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, default=0)
    max_score = db.Column(db.Float, default=0)
    passed_count = db.Column(db.Integer, default=0)
    total_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(40), default="Evaluated")
    tab_switches = db.Column(db.Integer, default=0)
    auto_submitted = db.Column(db.Boolean, default=False)
    auto_submit_reason = db.Column(db.String(60), nullable=True)  # "violations" | "time_expired"
    submitted_at = db.Column(db.DateTime, default=utcnow)


class AdminActivityLog(db.Model):
    __tablename__ = "admin_activity_log"
    id = db.Column(db.Integer, primary_key=True)
    admin_username = db.Column(db.String(80), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)


class Feedback(db.Model):
    __tablename__ = "feedback"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class AboutPage(db.Model):
    """Single-row table holding the editable About page content."""
    __tablename__ = "about_page"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), default="Divakar")
    bio = db.Column(db.Text, default="")
    photo_filename = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    linkedin_url = db.Column(db.String(255), nullable=True)
    github_url = db.Column(db.String(255), nullable=True)
    instagram_url = db.Column(db.String(255), nullable=True)
    twitter_url = db.Column(db.String(255), nullable=True)
    portfolio_url = db.Column(db.String(255), nullable=True)

    @staticmethod
    def get():
        a = AboutPage.query.first()
        if not a:
            a = AboutPage(name="Divakar")
            db.session.add(a)
            db.session.commit()
        return a


class Draft(db.Model):
    """Autosaved in-progress code, one row per (student, question)."""
    __tablename__ = "drafts"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    code = db.Column(db.Text, default="")
    language_id = db.Column(db.Integer, default=71)
    updated_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (db.UniqueConstraint("student_id", "question_id", name="uq_student_question"),)