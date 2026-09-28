# syntax=docker/dockerfile:1
FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANSIBLE_COLLECTIONS_PATH=/opt/ansible/collections \
    NARSIKA_DATA_DIR=/var/lib/narsika \
    NARSIKA_BACKUP_DIR=/var/lib/narsika/backups \
    NARSIKA_HTTP_BIND=0.0.0.0:8000
WORKDIR /opt/narsika
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client iputils-ping libssh-4 ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints.txt ./
COPY tools/dependencies.py /opt/narsika/tools/dependencies.py
ARG NARSIKA_PIP_INDEX_URL=https://pypi.org/simple/
ARG NARSIKA_PIP_TIMEOUT=120
ARG NARSIKA_PIP_RETRIES=3
# Download cache belongs to BuildKit, not the shipped image.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -c "from pathlib import Path; from tools.dependencies import install, settings; import sys; install(sys.executable, Path('requirements.txt'), settings(), interactive=False)"
COPY Playbooks/requirements.yml Playbooks/collections.lock.json /opt/narsika/Playbooks/
COPY ansible.cfg /opt/narsika/ansible.cfg
COPY tools/patch_ansible.py tools/ansible_environment.py tools/install_collections.py /opt/narsika/tools/
RUN python tools/install_collections.py --collections-path /opt/ansible/collections
RUN groupadd --gid 10001 narsika && useradd --uid 10001 --gid narsika --create-home --shell /usr/sbin/nologin narsika && mkdir -p /var/lib/narsika/backups && chown -R narsika:narsika /var/lib/narsika
COPY --chown=root:root app ./app
COPY --chown=root:root Playbooks ./Playbooks
COPY --chown=root:root callback_plugins ./callback_plugins
COPY --chown=root:root tools/maintenance.py tools/restore_snapshot.py tools/snapshot.py tools/bootstrap.py ./tools/
COPY --chown=root:root LICENSE runtime.py wsgi.py app.py gunicorn.conf.py ./
RUN ln -s Playbooks/Original /opt/narsika/playbooks
USER narsika
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["python", "app.py"]
