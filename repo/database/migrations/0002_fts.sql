-- Full-text search shadow tables.
CREATE VIRTUAL TABLE IF NOT EXISTS students_fts USING fts5(
    student_id_ext, full_name, college, content=''
);

CREATE VIRTUAL TABLE IF NOT EXISTS resources_fts USING fts5(
    title, summary, content=''
);

CREATE VIRTUAL TABLE IF NOT EXISTS employers_fts USING fts5(
    name, ein, content=''
);

CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    employer_name, kind, state, notes, content=''
);
