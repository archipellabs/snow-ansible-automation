# rulebooks/ — Event-Driven Ansible rulebooks

The EDA rulebooks the activations run (pulled from this repo by the EDA project):

- **`pull_incident_remediation.yml`** — poll ServiceNow (`servicenow.itsm.records`) → launch the job.
- **`monitor_health_open_incident.yml`** — self-driving monitor (`ansible.eda.url_check`) → open incident.
- **`push_change_execution.yml`** · **`push_selfservice_restart.yml`** · **`push_employee_onboarding.yml`**
  — webhook sources (`ansible.eda.webhook`) fed by the gateway Event Streams.

📖 **The runtime flows → [docs/04-patterns.md](../../../docs/04-patterns.md)** · how the activations are
created (declarative GitOps) → [docs/03-architecture.md](../../../docs/03-architecture.md).
