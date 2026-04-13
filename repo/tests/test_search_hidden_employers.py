"""Throttled / suspended / taken-down employers must be hidden by default."""
from __future__ import annotations


def test_default_search_hides_throttled(container, admin_session):
    container.compliance.submit_employer(admin_session,
                                         "Visible Corp", None, None)
    container.compliance.submit_employer(admin_session,
                                         "Hidden Corp", None, None)
    emps = container.compliance.list_employers(admin_session)
    hidden_id = [e["id"] for e in emps if e["name"] == "Hidden Corp"][0]
    container.violations.throttle(admin_session, hidden_id, "spam")

    hits = container.search.global_search(admin_session, "Corp",
                                          types={"employer"}, fuzzy=False)
    titles = [h.title for h in hits]
    assert "Visible Corp" in titles
    assert "Hidden Corp" not in titles

    hits_all = container.search.global_search(admin_session, "Corp",
                                              types={"employer"},
                                              fuzzy=False, include_hidden=True)
    assert "Hidden Corp" in [h.title for h in hits_all]
