-- Additional permissions for chunk-2 modules.
INSERT OR IGNORE INTO permissions (code) VALUES
    ('catalog.write'),
    ('catalog.review'),
    ('catalog.publish'),
    ('compliance.evidence'),
    ('compliance.action'),
    ('bom.write'),
    ('bom.approve.first'),
    ('bom.approve.final'),
    ('update.apply');

-- Wire to roles
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='system_admin'), id FROM permissions
WHERE code IN ('catalog.write','catalog.review','catalog.publish',
               'compliance.evidence','compliance.action',
               'bom.write','bom.approve.first','bom.approve.final',
               'update.apply');

INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='academic_admin'), id FROM permissions
WHERE code IN ('catalog.write','catalog.review','catalog.publish');

INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='compliance_reviewer'), id FROM permissions
WHERE code IN ('compliance.evidence','compliance.action');

INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code='operations_analyst'), id FROM permissions
WHERE code IN ('bom.write','bom.approve.first');
