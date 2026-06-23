# Mail role — mail-01. FROM the shared base (so Ansible can SSH in), plus Postfix as the service
# the playbooks (re)start and health-check.
FROM localhost/meridian-base:latest

RUN dnf -y install postfix && dnf clean all && systemctl enable postfix

EXPOSE 25
CMD ["/sbin/init"]
