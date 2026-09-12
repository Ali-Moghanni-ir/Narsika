import os
from runtime import load_environment
load_environment()
bind=os.getenv('NARSIKA_HTTP_BIND',os.getenv('NARSIKA_BIND_ADDRESS','0.0.0.0')+':'+os.getenv('NARSIKA_PORT','8000'))
workers=1
worker_class='gthread'
threads=8
timeout=120
graceful_timeout=330
keepalive=5
preload_app=False
accesslog='-'
errorlog='-'
capture_output=True
umask=0o077
# No reverse-proxy headers are trusted by default.
forwarded_allow_ips=''

def worker_exit(server,worker):
    try:
        manager=worker.wsgi.extensions.get('jobs')
        if manager:manager.close()
    except (AttributeError,RuntimeError):pass
