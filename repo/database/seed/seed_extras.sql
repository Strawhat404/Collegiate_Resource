-- Seed for chunk-2 features. Idempotent via INSERT OR IGNORE.

INSERT OR IGNORE INTO catalog_nodes (id, parent_id, name, sort_order) VALUES
    (1, NULL, 'Curriculum',    0),
    (2, 1,    'Mathematics',   0),
    (3, 1,    'English',       1),
    (4, NULL, 'Career Center', 1);

INSERT OR IGNORE INTO catalog_types (code, name, description) VALUES
    ('syllabus',     'Syllabus',     'Course syllabus document'),
    ('practice_set', 'Practice Set', 'Problem sets and answer keys'),
    ('career_doc',   'Career Doc',   'Career-services materials');

-- Sample template for syllabi.
INSERT OR IGNORE INTO catalog_type_fields
    (type_id, code, label, field_type, regex, required, sort_order)
VALUES
    ((SELECT id FROM catalog_types WHERE code='syllabus'),
     'course_code', 'Course code', 'text', '^[A-Z]{2,4}-\d{3,4}$', 1, 0),
    ((SELECT id FROM catalog_types WHERE code='syllabus'),
     'credits', 'Credits', 'int', NULL, 1, 1),
    ((SELECT id FROM catalog_types WHERE code='syllabus'),
     'effective', 'Effective date', 'date', NULL, 1, 2);

-- A small offline sensitive-word dictionary (placeholder demo terms).
INSERT OR IGNORE INTO sensitive_words (word, severity, category) VALUES
    ('guarantee_employment', 'high',   'misleading_claim'),
    ('cash_only',            'medium', 'payment_practice'),
    ('ssn_required',         'high',   'pii_overreach'),
    ('background_optional',  'low',    'compliance');

-- Notification template for compliance evidence upload + violation actions.
INSERT OR IGNORE INTO notif_templates (name, subject, body, variables_json) VALUES
    ('evidence_uploaded',
     'Evidence uploaded for {EmployerName}',
     'New evidence file recorded for {EmployerName} on {Today}.',
     '["EmployerName","Today"]'),
    ('violation_action',
     'Violation action: {EmployerName}',
     'Action taken against {EmployerName}: see compliance queue. ({Today})',
     '["EmployerName","Today"]');

-- Idempotent role-permission grants for chunk-3 permissions. Run on every
-- startup so they bind even when added to an already-seeded database.
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r, permissions p
WHERE r.code='system_admin'
  AND p.code IN ('student.write','student.import');

INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r, permissions p
WHERE r.code='housing_coordinator'
  AND p.code IN ('student.write','student.import');
