<sub>[↑ Docs map](../README.md#start-here) · [← 03 · Architecture](03-architecture.md) · **04 · The patterns** · [05 · Identity →](05-identity.md)</sub>

# The patterns — runtime flows

The two halves of the demo, plus the variants built on top of them. The conceptual model is in
[01 · Overview](01-overview.md); this page walks the **runtime**, step by step.

## Pull — incident remediation

![Pull pattern — the auto-remediation loop](diagrams/remediation-flow.svg)

1. A monitored service goes down → an **incident** is opened in ServiceNow, assigned to the
   **Auto-Remediation** group (`state = New`). This can be opened by anyone, or **self-driven** (see
   the monitor loop below).
2. **EDA** polls ServiceNow (`servicenow.itsm.records`), detects the incident, and triggers the
   `Restart Service` **job template**, passing the incident number + the affected host.
3. The job runs `restart_service.yml` in an execution environment: SSH to the target, restart the
   service, re-check.
4. The playbook updates the incident via `servicenow.itsm`: **Resolved** if the service is back,
   otherwise **escalated**.

**Self-driving** — the `monitor-health` activation probes each app's `/health` with
`ansible.eda.url_check` and raises the incident itself (via the `Open Incident` job template →
[`open_incident.yml`](../playbooks/open_incident.yml)). So no human even opens the ticket: outage →
incident → remediation → resolved.

## Push — change execution

![Push pattern — change through an Event Stream](diagrams/change-flow.svg)

1. A **Change Request** is **approved** in ServiceNow (and references a CI target).
2. A **Business Rule** POSTs the change (number, sys_id, target) to an AAP **Event Stream** — a
   gateway-managed webhook endpoint on `:443` with token auth.
3. The event stream feeds the `ansible.eda.webhook` source of the `push-change-execution` activation,
   which triggers the `Execute Change Request` **job template**.
4. The job runs `execute_change.yml`: deploy the change to the target, restart the service, and write
   the result back to the change as a **work note**.

## Push — self-service catalog

1. A user orders the **Service Catalog** item "Restart a service" and picks a server.
2. A **Business Rule** on the request item (`sc_req_item`) POSTs the chosen server to a second AAP
   **Event Stream** (`servicenow-catalog-stream`, same `:443` token endpoint).
3. The stream feeds the `ansible.eda.webhook` source of the `push-selfservice-restart` activation,
   which triggers the `Restart Service (Self-Service)` **job template**.
4. `restart_service_selfservice.yml` restarts that server's service and **closes the request item**.

## Push — employee onboarding

The same catalog mechanism, but the request creates an **identity** instead of restarting a service:
ordering "Onboard a new employee" pushes to the `servicenow-onboarding-stream`, the
`push-employee-onboarding` activation runs the `Provision Employee` job template →
[`provision_employee.yml`](../playbooks/provision_employee.yml) creates the Keycloak user (in the
*Employees* group) and closes the request. See [05 · Identity](05-identity.md).

---

All five activations are created declaratively (the **Configure EDA** job template); the catalog
items and Business Rules are set up by `bootstrap/4_servicenow/4_catalog.py`. Each flow has a
re-runnable scenario test — see [09 · Tests](09-tests.md).

---
<sub>[↑ Docs map](../README.md#start-here) · [← 03 · Architecture](03-architecture.md) · **04 · The patterns** · [05 · Identity →](05-identity.md)</sub>
