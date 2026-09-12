"""Installation failures must be early, non-destructive and independent of host Python."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]


def configuration(tmp_path):
    shutil.copy(ROOT/'configure.py',tmp_path/'configure.py')
    env={**os.environ,'NARSIKA_ADMIN_PASSWORD':'test-install-password-$-quotes'}
    subprocess.run([sys.executable,str(tmp_path/'configure.py')],env=env,check=True,capture_output=True)
    return tmp_path/'.env'


@pytest.mark.parametrize('entry',[
    'NARSIKA_PORT=wrong','NARSIKA_PORT=65536','NARSIKA_ENCRYPTION_KEY=invalid',
    'NARSIKA_SECRET_KEY=short','NARSIKA_JOB_TIMEOUT=301','NARSIKA_TRUST_PROXY_HOPS=2',
    'NARSIKA_ALLOWED_NETWORKS=::1/128','NARSIKA_ADMIN_PASSWORD_BASE64=%%%','NARSIKA_SCAN_MAX_HOSTS=0'])
def test_invalid_configuration_is_not_modified(tmp_path,entry):
    path=configuration(tmp_path);key=entry.partition('=')[0]
    lines=path.read_text().splitlines()
    path.write_text('\n'.join(line for line in lines if not line.startswith(key+'='))+'\n'+entry+'\n')
    before=path.read_bytes()
    result=subprocess.run([sys.executable,str(tmp_path/'configure.py'),'--check'],capture_output=True,text=True)
    assert result.returncode!=0
    assert path.read_bytes()==before
    assert 'test-install-password' not in result.stdout+result.stderr


def test_valid_configuration_and_bootstrap_cleanup(tmp_path):
    path=configuration(tmp_path)
    for arguments in (['--check'],['--clear-bootstrap'],['--check']):
        subprocess.run([sys.executable,str(tmp_path/'configure.py'),*arguments],check=True,capture_output=True)
    assert 'NARSIKA_ADMIN_PASSWORD' not in path.read_text()


@pytest.mark.parametrize('missing_engine',[False,True])
@pytest.mark.parametrize('fail_step,successful', [('none',True),('build',False),('configuration',False),('health',False)])
def test_linux_launcher_no_host_python_and_failure_propagation(tmp_path,fail_step,successful,missing_engine):
    project=tmp_path/'project with spaces';project.mkdir()
    shutil.copy(ROOT/'run_linux.sh',project/'run_linux.sh')
    (project/'tools').mkdir()
    shutil.copy(ROOT/'tools/run_docker_linux.sh',project/'tools/run_docker_linux.sh')
    programs=tmp_path/'bin';programs.mkdir()
    # Only expose shell utilities and a controlled Docker process, deliberately no Python.
    for name in ('bash','dirname','id','grep'):
        (programs/name).symlink_to(shutil.which(name))
    docker=programs/'docker'
    docker.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$INSTALL_LOG"
case "$*" in
  'info --format '* ) echo linux ;;
  'compose up --help') echo --wait-timeout ;;
  'compose port '*) echo 127.0.0.1:8123 ;;
  'build '*) [[ "$FAIL_STEP" != build ]] ;;
  'run '*) [[ "$FAIL_STEP" != configuration ]] ;;
  'compose up -d '*) [[ "$FAIL_STEP" != health ]] ;;
  *) exit 0 ;;
esac
''');docker.chmod(0o755)
    if missing_engine:
        # Simulate package installation without modifying the test host's system files.
        docker.rename(programs/'docker-ready')
        stubs={
            'sudo':'if [[ "$1" == -v ]]; then exit 0; fi; exec "$@"',
            'apt-get':'printf "package %s\\n" "$*" >> "$INSTALL_LOG"; if [[ "$*" == *docker-ce* ]]; then /bin/cp "$INSTALL_BIN/docker-ready" "$INSTALL_BIN/docker"; fi; exit 0',
            'dpkg':'echo amd64', 'install':'exit 0', 'curl':'exit 0', 'chmod':'exit 0',
            'tee':'while IFS= read -r line; do :; done',
        }
        for name,body in stubs.items():
            target=programs/name;target.write_text('#!/bin/bash\n'+body+'\n');target.chmod(0o755)
    log=tmp_path/'commands' 
    result=subprocess.run([str(programs/'bash'),str(project/'run_linux.sh'),'--docker'],env={**os.environ,'PATH':str(programs),'FAIL_STEP':fail_step,'INSTALL_LOG':str(log),'INSTALL_BIN':str(programs)},capture_output=True,text=True)
    assert (result.returncode==0)==successful,result.stdout+result.stderr
    assert ('Narsika is healthy' in result.stdout)==successful
    commands=log.read_text()
    if missing_engine:assert 'package install -y docker-ce' in commands
    if fail_step=='build':assert 'run --rm' not in commands
    if fail_step=='configuration':assert 'compose up -d' not in commands
    if successful:assert 'http://127.0.0.1:8123' in result.stdout
