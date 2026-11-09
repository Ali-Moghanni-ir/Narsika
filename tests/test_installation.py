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


def without_proxy():
    return {k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','no_proxy','all_proxy')}


@pytest.mark.parametrize('extra_env',[{},{'HTTPS_PROXY':'http://127.0.0.1:3128'},{'BUILD_CAUSE':'Cannot resolve files.pythonhosted.org'}],ids=['direct','local-proxy','build-cause'])
@pytest.mark.parametrize('missing_engine',[False,True])
@pytest.mark.parametrize('fail_step,successful', [('none',True),('network',False),('build',False),('configuration',False),('health',False)])
def test_linux_launcher_no_host_python_and_failure_propagation(tmp_path,fail_step,successful,missing_engine,extra_env):
    project=tmp_path/'project with spaces';project.mkdir()
    shutil.copy(ROOT/'run_linux.sh',project/'run_linux.sh')
    (project/'tools').mkdir()
    shutil.copy(ROOT/'tools/run_docker_linux.sh',project/'tools/run_docker_linux.sh')
    shutil.copy(ROOT/'Dockerfile',project/'Dockerfile')
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
  *narsika-network-check.py) [[ "$FAIL_STEP" != network ]] || { echo 'NARSIKA_SETUP_CAUSE: Cannot reach files.pythonhosted.org (Python package files): DNS name resolution failed.'; exit 3; } ;;
  'build '*) [[ -z "$BUILD_CAUSE" ]] || echo "#14 3.2 NARSIKA_SETUP_CAUSE: $BUILD_CAUSE"; [[ "$FAIL_STEP" != build ]] ;;
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
    result=subprocess.run([str(programs/'bash'),str(project/'run_linux.sh'),'--docker'],env={**without_proxy(),**extra_env,'PATH':str(programs),'FAIL_STEP':fail_step,'INSTALL_LOG':str(log),'INSTALL_BIN':str(programs),'NARSIKA_PIP_INDEX_URL':'https://mirror.example.org/simple/','NARSIKA_PIP_TIMEOUT':'45','NARSIKA_PIP_RETRIES':'2'},capture_output=True,text=True)
    assert (result.returncode==0)==successful,result.stdout+result.stderr
    assert ('Narsika is healthy' in result.stdout)==successful
    commands=log.read_text()
    if fail_step=='network':
        # The long build never starts, and the message names the unreachable host.
        assert 'build --progress' not in commands
        assert 'Narsika setup stopped: Cannot reach files.pythonhosted.org' in result.stderr
        return
    assert '--build-arg NARSIKA_PIP_INDEX_URL=https://mirror.example.org/simple/' in commands
    assert '--build-arg NARSIKA_PIP_TIMEOUT=45' in commands
    assert '--build-arg NARSIKA_PIP_RETRIES=2' in commands
    assert 'build --progress=plain' in commands
    if missing_engine:assert 'package install -y docker-ce' in commands
    if fail_step=='build':assert 'configure.py' not in commands
    if fail_step=='build' and extra_env.get('BUILD_CAUSE'):
        assert 'Narsika setup stopped: '+extra_env['BUILD_CAUSE'] in result.stderr
    if 'HTTPS_PROXY' in extra_env:
        assert '--build-arg HTTPS_PROXY=http://127.0.0.1:3128' in commands and '--build-arg NO_PROXY=localhost,127.0.0.1' in commands
        assert 'build --progress=plain --network host' in commands
        assert '--network host -e HTTP_PROXY=http://127.0.0.1:3128' in commands and 'network_check.py' in commands
    else:
        assert 'PROXY' not in commands and '--network host' not in commands
    # Runtime containers never receive the build proxy.
    assert not [line for line in commands.splitlines() if line.startswith('compose') and 'PROXY' in line]
    # The network check runs first, from the pinned base image.
    lines=commands.splitlines()
    check=next(i for i,line in enumerate(lines) if 'network_check.py' in line)
    assert '@sha256:' in lines[check] and all(not line.startswith('build ') for line in lines[:check])
    if fail_step=='configuration':assert 'compose up -d' not in commands
    if successful:assert 'http://127.0.0.1:8123' in result.stdout
