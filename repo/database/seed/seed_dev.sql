-- Demo seed data. Run only against an empty database.

INSERT INTO roles (code, name) VALUES
    ('system_admin',          'System Administrator'),
    ('housing_coordinator',   'Housing Coordinator'),
    ('academic_admin',        'Academic Admin'),
    ('compliance_reviewer',   'Compliance Reviewer'),
    ('operations_analyst',    'Operations Analyst');

INSERT INTO permissions (code) VALUES
    ('system.admin'),
    ('housing.write'),
    ('student.pii.read'),
    ('resource.write'),
    ('resource.publish'),
    ('compliance.review'),
    ('compliance.violation'),
    ('report.read'),
    ('report.export'),
    ('notification.admin');

-- System admin gets everything
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='system_admin'), id FROM permissions;

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='housing_coordinator'),
       id FROM permissions WHERE code IN ('housing.write','student.pii.read');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='academic_admin'),
       id FROM permissions WHERE code IN ('resource.write','resource.publish');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='compliance_reviewer'),
       id FROM permissions WHERE code IN ('compliance.review','compliance.violation');

INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='operations_analyst'),
       id FROM permissions WHERE code IN ('report.read','report.export');

-- Buildings / rooms / beds
INSERT INTO buildings (name, address) VALUES
    ('Whitman Hall',  '101 Campus Way'),
    ('Lincoln House', '202 College St');

INSERT INTO rooms (building_id, floor, code) VALUES
    (1, 1, '101'), (1, 1, '102'), (1, 2, '201'),
    (2, 1, '110'), (2, 2, '210');

INSERT INTO beds (room_id, code, capacity) VALUES
    (1, 'A', 1), (1, 'B', 1),
    (2, 'A', 1), (2, 'B', 1),
    (3, 'A', 1),
    (4, 'A', 1), (4, 'B', 1),
    (5, 'A', 1);

-- Resource categories
INSERT INTO resource_categories (name) VALUES
    ('Syllabi'), ('Practice Sets'), ('Course Notes'), ('Career Resources');

-- Notification templates
INSERT INTO notif_templates (name, subject, body, variables_json) VALUES
    ('bed_assigned',
     'Bed assignment confirmed',
     'Hello {StudentName}, you have been assigned to {Dorm} room/bed {Room}/{Bed} effective {EffectiveDate}.',
     '["StudentName","Dorm","Room","Bed","EffectiveDate"]'),
    ('resource_published',
     'New resource available',
     'A new resource "{ResourceTitle}" has been published by {Operator} on {Today}.',
     '["ResourceTitle","Operator","Today"]'),
    ('compliance_decision',
     'Compliance decision recorded',
     'Employer {EmployerName} case decision: see Compliance queue. ({Today})',
     '["EmployerName","Today"]'),
    ('daily_digest',
     'Daily operations digest — {Today}',
     'Your daily summary for {Today} is ready in the Reports tab.',
     '["Today"]');

INSERT INTO notif_rules (name, kind, event_name, template_id, audience_query, enabled) VALUES
    ('On bed assignment', 'trigger', 'BED_ASSIGNED',
     (SELECT id FROM notif_templates WHERE name='bed_assigned'),
     '{"role":"housing_coordinator"}', 1),
    ('On resource publish', 'trigger', 'RESOURCE_PUBLISHED',
     (SELECT id FROM notif_templates WHERE name='resource_published'),
     '{"role":"academic_admin"}', 1),
    ('On compliance decision', 'trigger', 'CASE_DECIDED',
     (SELECT id FROM notif_templates WHERE name='compliance_decision'),
     '{"role":"compliance_reviewer"}', 1);

INSERT INTO notif_rules (name, kind, cron_spec, template_id, audience_query, enabled) VALUES
    ('Daily 7am digest', 'schedule', '0 7 * * *',
     (SELECT id FROM notif_templates WHERE name='daily_digest'),
     '{"role":"operations_analyst"}', 1);

-- Synonyms
INSERT INTO synonyms (term, alt_term) VALUES
    ('dorm', 'residence'),
    ('dorm', 'hall'),
    ('syllabus', 'syllabi'),
    ('freshman', 'first-year');
