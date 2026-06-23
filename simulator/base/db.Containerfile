# Database role — hr-db-01 / crm-db-01 / ged-db-01. FROM the shared base (so Ansible can SSH in),
# plus a REAL PostgreSQL server. UBI's appstream ships only the PostgreSQL client, so the server
# comes from the PostgreSQL community repository (PGDG). A real server means admin playbooks work
# (create roles/users, schema changes), not just service restarts.
#
# The PGDG systemd unit is postgresql-16; we alias it to `postgresql` so the manifest/CMDB can keep
# the generic service name. initdb runs directly (the postgresql-16-setup wrapper needs a running
# systemd bus, which isn't available at build time). Data dir: /var/lib/pgsql/16/data.
FROM localhost/meridian-base:latest
ARG PG=16

RUN dnf -y install https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm \
    && (dnf -qy module disable postgresql 2>/dev/null || true) \
    && dnf -y install postgresql${PG}-server postgresql${PG} \
    && dnf clean all

RUN mkdir -p /var/lib/pgsql/${PG}/data \
    && chown -R postgres:postgres /var/lib/pgsql/${PG} \
    && su - postgres -c "/usr/pgsql-${PG}/bin/initdb -D /var/lib/pgsql/${PG}/data" \
    && ln -sf /usr/lib/systemd/system/postgresql-${PG}.service /etc/systemd/system/multi-user.target.wants/postgresql-${PG}.service \
    && ln -sf /usr/lib/systemd/system/postgresql-${PG}.service /etc/systemd/system/postgresql.service

EXPOSE 5432
CMD ["/sbin/init"]
