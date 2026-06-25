# rulebooks/ — Event-Driven Ansible rulebooks

The EDA rulebooks the activations run (pulled from this repo by the EDA project):

- **`pull_incident_remediation.yml`** — poll ServiceNow incidents (`servicenow.itsm.records`) → launch the job.
- **`pull_selfservice_restart.yml`** · **`pull_employee_onboarding.yml`** — poll catalog requests
  (`servicenow.itsm.records` on `sc_req_item`); the job's playbook fetches the form's variables by `request_sysid`.
- **`monitor_health_open_incident.yml`** — self-driving monitor (`ansible.eda.url_check`) → open incident.
- **`push_change_execution.yml`** — the one webhook source (`ansible.eda.webhook`), fed by a gateway
  Event Stream when a Change Request is approved.

📖 **The runtime flows → [docs/04-patterns.md](../../../docs/04-patterns.md)** · how the activations are
created (declarative GitOps) → [docs/03-architecture.md](../../../docs/03-architecture.md).
