"""Load the installation configuration without shell expansion or a dotenv dependency."""
import base64
import os
from pathlib import Path

BOOTSTRAP_KEYS={'NARSIKA_ADMIN_PASSWORD','NARSIKA_ADMIN_PASSWORD_BASE64'}

def load_environment(include_bootstrap=False):
    path=Path(os.getenv('NARSIKA_ENV_FILE', str(Path(__file__).resolve().parent/'.env')))
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key,separator,value=line.partition('=')
                if separator and key.startswith('NARSIKA_') and (include_bootstrap or key not in BOOTSTRAP_KEYS):os.environ.setdefault(key,value)
    if include_bootstrap and not os.getenv('NARSIKA_ADMIN_PASSWORD') and os.getenv('NARSIKA_ADMIN_PASSWORD_BASE64'):
        os.environ['NARSIKA_ADMIN_PASSWORD']=base64.b64decode(os.environ['NARSIKA_ADMIN_PASSWORD_BASE64'],validate=True).decode()
    os.umask(0o077)
