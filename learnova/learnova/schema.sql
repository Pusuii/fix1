-- Learnova LMS - skema database (SQLite)
-- Mengikuti ERD Learnova: 21 tabel dengan nama tabel & kolom yang sama.
-- BIGINT/INT pada ERD dipetakan ke INTEGER, VARCHAR/TEXT ke TEXT, DECIMAL ke REAL,
-- DATETIME disimpan sebagai teks 'YYYY-MM-DD HH:MM:SS'.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS roles (
    role_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name     TEXT NOT NULL UNIQUE,
    description   TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id         INTEGER NOT NULL REFERENCES roles(role_id),
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    account_status  TEXT NOT NULL DEFAULT 'pending',   -- pending | aktif | nonaktif | ditolak
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    permission_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    permission_name  TEXT NOT NULL UNIQUE,
    description      TEXT
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id        INTEGER NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id  INTEGER NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS courses (
    course_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name  TEXT NOT NULL,
    course_code  TEXT NOT NULL UNIQUE,
    description  TEXT
);

CREATE TABLE IF NOT EXISTS classes (
    class_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id      INTEGER NOT NULL REFERENCES courses(course_id),
    lecturer_id    INTEGER NOT NULL REFERENCES users(user_id),
    class_name     TEXT NOT NULL,
    semester       TEXT NOT NULL,
    academic_year  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'aktif'       -- aktif | nonaktif | selesai
);

CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id       INTEGER NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    student_id     INTEGER NOT NULL REFERENCES users(user_id),
    enrolled_at    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'aktif',      -- aktif | keluar
    UNIQUE (class_id, student_id)
);

CREATE TABLE IF NOT EXISTS materials (
    material_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id         INTEGER NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    description      TEXT,
    file_url         TEXT,
    material_type    TEXT NOT NULL DEFAULT 'dokumen',  -- dokumen | slide | video | tautan
    sequence_no      INTEGER NOT NULL DEFAULT 1,
    prerequisite_id  INTEGER REFERENCES materials(material_id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS material_progress (
    progress_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id  INTEGER NOT NULL REFERENCES enrollments(enrollment_id) ON DELETE CASCADE,
    material_id    INTEGER NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    is_completed   INTEGER NOT NULL DEFAULT 0,
    completed_at   TEXT,
    UNIQUE (enrollment_id, material_id)
);

CREATE TABLE IF NOT EXISTS class_sessions (
    session_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id         INTEGER NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    session_date     TEXT NOT NULL,
    topic            TEXT NOT NULL,
    attendance_open  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attendances (
    attendance_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         INTEGER NOT NULL REFERENCES class_sessions(session_id) ON DELETE CASCADE,
    student_id         INTEGER NOT NULL REFERENCES users(user_id),
    attendance_status  TEXT NOT NULL DEFAULT 'hadir',  -- hadir | izin | sakit | alpa
    recorded_at        TEXT NOT NULL,
    UNIQUE (session_id, student_id)
);

CREATE TABLE IF NOT EXISTS assignments (
    assignment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id       INTEGER NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    instructions   TEXT,
    deadline       TEXT NOT NULL,
    max_score      REAL NOT NULL DEFAULT 100,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    submission_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id  INTEGER NOT NULL REFERENCES assignments(assignment_id) ON DELETE CASCADE,
    student_id     INTEGER NOT NULL REFERENCES users(user_id),
    file_url       TEXT NOT NULL,
    submitted_at   TEXT NOT NULL,
    score          REAL,
    feedback       TEXT,
    status         TEXT NOT NULL DEFAULT 'dikumpulkan', -- dikumpulkan | dinilai
    UNIQUE (assignment_id, student_id)
);

CREATE TABLE IF NOT EXISTS quizzes (
    quiz_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id          INTEGER NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    duration_minutes  INTEGER NOT NULL DEFAULT 30,
    deadline          TEXT NOT NULL,
    max_score         REAL NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    question_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id        INTEGER NOT NULL REFERENCES quizzes(quiz_id) ON DELETE CASCADE,
    question_text  TEXT NOT NULL,
    question_type  TEXT NOT NULL DEFAULT 'pilihan_ganda', -- pilihan_ganda | esai
    points         REAL NOT NULL DEFAULT 10
);

CREATE TABLE IF NOT EXISTS quiz_options (
    option_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id  INTEGER NOT NULL REFERENCES quiz_questions(question_id) ON DELETE CASCADE,
    option_text  TEXT NOT NULL,
    is_correct   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    attempt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id       INTEGER NOT NULL REFERENCES quizzes(quiz_id) ON DELETE CASCADE,
    student_id    INTEGER NOT NULL REFERENCES users(user_id),
    started_at    TEXT NOT NULL,
    submitted_at  TEXT,
    score         REAL,
    attempt_no    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    answer_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id          INTEGER NOT NULL REFERENCES quiz_attempts(attempt_id) ON DELETE CASCADE,
    question_id         INTEGER NOT NULL REFERENCES quiz_questions(question_id) ON DELETE CASCADE,
    selected_option_id  INTEGER REFERENCES quiz_options(option_id) ON DELETE SET NULL,
    answer_text         TEXT,
    is_correct          INTEGER,
    score_awarded       REAL,                           -- NULL = menunggu penilaian dosen
    UNIQUE (attempt_id, question_id)
);

CREATE TABLE IF NOT EXISTS user_reports (
    report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id  INTEGER NOT NULL REFERENCES users(user_id),
    handled_by   INTEGER REFERENCES users(user_id),
    subject      TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'baru',          -- baru | diproses | selesai
    created_at   TEXT NOT NULL,
    response     TEXT                                   -- tambahan: tanggapan penangan laporan
);

CREATE TABLE IF NOT EXISTS system_settings (
    setting_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key    TEXT NOT NULL UNIQUE,
    setting_value  TEXT,
    updated_by     INTEGER REFERENCES users(user_id),
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id  INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    action         TEXT NOT NULL,
    target_type    TEXT,
    target_id      INTEGER,
    details        TEXT,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_role        ON users(role_id);
CREATE INDEX IF NOT EXISTS idx_classes_lecturer  ON classes(lecturer_id);
CREATE INDEX IF NOT EXISTS idx_enroll_student    ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_materials_class   ON materials(class_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_sessions_class    ON class_sessions(class_id);
CREATE INDEX IF NOT EXISTS idx_assign_class      ON assignments(class_id);
CREATE INDEX IF NOT EXISTS idx_quiz_class        ON quizzes(class_id);
CREATE INDEX IF NOT EXISTS idx_attempt_quiz      ON quiz_attempts(quiz_id, student_id);
CREATE INDEX IF NOT EXISTS idx_audit_created     ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_reports_status    ON user_reports(status);
