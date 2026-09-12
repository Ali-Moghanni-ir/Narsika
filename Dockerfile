FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ANSIBLE_COLLECTIONS_PATH=/opt/ansible/collections \
    NARSIKA_DATA_DIR=/var/lib/narsika \
    NARSIKA_BACKUP_DIR=/var/lib/narsika/backups \
    NARSIKA_HTTP_BIND=0.0.0.0:8000
WORKDIR /opt/narsika
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client iputils-ping libssh-4 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints.txt ./
RUN pip install --requirement requirements.txt
COPY Playbooks/requirements.yml /opt/narsika/Playbooks/requirements.yml
COPY tools/patch_ansible.py /opt/narsika/tools/patch_ansible.py
RUN ansible-galaxy collection install --requirements-file Playbooks/requirements.yml --collections-path /opt/ansible/collections && python tools/patch_ansible.py /opt/ansible/collections
RUN groupadd --gid 10001 narsika && useradd --uid 10001 --gid narsika --create-home --shell /usr/sbin/nologin narsika && mkdir -p /var/lib/narsika/backups && chown -R narsika:narsika /var/lib/narsika
COPY --chown=root:root app ./app
COPY --chown=root:root Playbooks ./Playbooks
COPY --chown=root:root callback_plugins ./callback_plugins
COPY --chown=root:root tools/maintenance.py tools/restore_snapshot.py tools/snapshot.py tools/bootstrap.py ./tools/
COPY --chown=root:root runtime.py wsgi.py app.py gunicorn.conf.py ./
RUN ln -s Playbooks/Original /opt/narsika/playbooks
USER narsika
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["python", "app.py"]
