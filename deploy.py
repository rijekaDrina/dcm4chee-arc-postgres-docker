#!/usr/bin/env python3
"""Install the secure dcm4chee/PostgreSQL Compose stack with local secrets."""
from __future__ import annotations

import argparse
import gzip
import ipaddress
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / '.env'
PORTS = (8443, 8843, 9993, 11112, 2762, 2575, 12575)
IMAGES = ('dcm4che/dcm4chee-arc-psql:5.35.1-secure', 'dcm4che/keycloak:25.0.6',
          'dcm4che/slapd-dcm4chee:2.6.13-35.1', 'dcm4che/postgres-dcm4chee:18.3-35', 'mariadb:10.11')


def run(*args: str, capture: bool = False, check: bool = True, input_data: bytes | None = None):
    result = subprocess.run(args, cwd=ROOT, capture_output=capture, input=input_data,
                            text=input_data is None, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or '') if capture else ''
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def title(message: str):
    print(f'\n== {message} ==', flush=True)


def prompt(message: str, default: str) -> str:
    return input(f'{message} [{default}]: ').strip() or default


def host_ipv4() -> str:
    addresses = run('ip', '-4', '-o', 'addr', 'show', 'scope', 'global', capture=True).stdout
    for line in addresses.splitlines():
        fields = line.split()
        if not fields[1].startswith(('docker', 'br-', 'veth', 'tun', 'wg', 'zt')):
            return fields[3].split('/')[0]
    raise RuntimeError('No suitable IPv4 address found. Pass --host-ip explicitly.')


def memory_values() -> dict[str, str]:
    total_gb = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                        if line.startswith('MemTotal:'))) / 1024 / 1024
    if total_gb < 5:
        raise RuntimeError(f'{total_gb:.1f} GiB RAM detected. At least 5 GiB is needed; 8 GiB or more is recommended.')
    if total_gb < 7:
        return dict(PG_SHARED_BUFFERS='512MB', PG_EFFECTIVE_CACHE_SIZE='2GB', PG_WORK_MEM='4MB',
                    PG_MAINTENANCE_WORK_MEM='128MB', PG_MAX_CONNECTIONS='40', PG_MEMORY_LIMIT='1536m',
                    PG_SHM_SIZE='256m', PG_MAX_WAL_SIZE='1GB', PG_MIN_WAL_SIZE='256MB', ARC_HEAP_MB='768', ARC_MEMORY_LIMIT='1280m',
                    KC_HEAP_MB='512', KC_MEMORY_LIMIT='768m', MARIADB_MEMORY_LIMIT='768m')
    if total_gb < 14:
        return dict(PG_SHARED_BUFFERS='1GB', PG_EFFECTIVE_CACHE_SIZE='3GB', PG_WORK_MEM='4MB',
                    PG_MAINTENANCE_WORK_MEM='256MB', PG_MAX_CONNECTIONS='60', PG_MEMORY_LIMIT='3g',
                    PG_SHM_SIZE='512m', PG_MAX_WAL_SIZE='2GB', PG_MIN_WAL_SIZE='512MB', ARC_HEAP_MB='1024', ARC_MEMORY_LIMIT='2g',
                    KC_HEAP_MB='512', KC_MEMORY_LIMIT='1g', MARIADB_MEMORY_LIMIT='1g')
    if total_gb < 28:
        return dict(PG_SHARED_BUFFERS='3GB', PG_EFFECTIVE_CACHE_SIZE='8GB', PG_WORK_MEM='8MB',
                    PG_MAINTENANCE_WORK_MEM='512MB', PG_MAX_CONNECTIONS='100', PG_MEMORY_LIMIT='6g',
                    PG_SHM_SIZE='1g', PG_MAX_WAL_SIZE='4GB', PG_MIN_WAL_SIZE='1GB', ARC_HEAP_MB='2048', ARC_MEMORY_LIMIT='3g',
                    KC_HEAP_MB='768', KC_MEMORY_LIMIT='1280m', MARIADB_MEMORY_LIMIT='1g')
    return dict(PG_SHARED_BUFFERS='8GB', PG_EFFECTIVE_CACHE_SIZE='16GB', PG_WORK_MEM='8MB',
                PG_MAINTENANCE_WORK_MEM='512MB', PG_MAX_CONNECTIONS='100', PG_MEMORY_LIMIT='12g',
                PG_SHM_SIZE='1g', PG_MAX_WAL_SIZE='8GB', PG_MIN_WAL_SIZE='2GB', ARC_HEAP_MB='3072', ARC_MEMORY_LIMIT='4g',
                KC_HEAP_MB='1024', KC_MEMORY_LIMIT='1536m', MARIADB_MEMORY_LIMIT='1g')



def storage_values(choice: str) -> dict[str, str]:
    if choice == 'auto':
        source = run('df', '-P', str(ROOT), capture=True).stdout.splitlines()[-1].split()[0]
        probe = run('lsblk', '-n', '-o', 'ROTA', source, capture=True, check=False)
        choice = 'ssd' if probe.returncode == 0 and probe.stdout.splitlines() and probe.stdout.splitlines()[0].strip() == '0' else 'hdd'
    if choice == 'ssd':
        return dict(PG_RANDOM_PAGE_COST='1.1', PG_EFFECTIVE_IO_CONCURRENCY='200')
    return dict(PG_RANDOM_PAGE_COST='4.0', PG_EFFECTIVE_IO_CONCURRENCY='1')

def ensure_tools() -> None:
    missing = [name for name in ('docker', 'openssl', 'ip') if not shutil.which(name)]
    if shutil.which('docker') and run('docker', 'compose', 'version', capture=True, check=False).returncode:
        missing.append('docker compose plugin')
    if not missing:
        if run('docker', 'info', capture=True, check=False).returncode:
            if os.geteuid():
                raise RuntimeError('Docker is installed but not running. Start it with sudo systemctl enable --now docker.')
            run('systemctl', 'enable', '--now', 'docker')
        run('docker', 'info', capture=True)
        return
    print('Missing:', ', '.join(missing), flush=True)
    if os.geteuid():
        raise RuntimeError('Run sudo python3 deploy.py so the wizard can install missing system packages.')
    release = Path('/etc/os-release').read_text().lower()
    if any(token in release for token in ('id=almalinux', 'id=rocky', 'id=rhel', 'id=centos')):
        run('dnf', '-y', 'install', 'dnf-plugins-core', 'openssl', 'iproute')
        repo = 'https://download.docker.com/linux/centos/docker-ce.repo'
        if run('dnf', 'config-manager', '--add-repo', repo, capture=True, check=False).returncode:
            run('dnf', 'config-manager', 'addrepo', '--from-repofile', repo)
        run('dnf', '-y', 'install', 'docker-ce', 'docker-ce-cli', 'containerd.io',
            'docker-buildx-plugin', 'docker-compose-plugin')
    elif any(token in release for token in ('id=ubuntu', 'id=debian')):
        distro = 'ubuntu' if 'id=ubuntu' in release else 'debian'
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', 'ca-certificates', 'curl', 'openssl', 'iproute2')
        keyring = Path('/etc/apt/keyrings')
        keyring.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(f'https://download.docker.com/linux/{distro}/gpg', keyring / 'docker.asc')
        (keyring / 'docker.asc').chmod(0o644)
        release_values = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        codename = release_values.get('UBUNTU_CODENAME', release_values.get('VERSION_CODENAME', '')).strip('\"')
        arch = run('dpkg', '--print-architecture', capture=True).stdout.strip()
        Path('/etc/apt/sources.list.d/docker.sources').write_text(
            f'Types: deb\nURIs: https://download.docker.com/linux/{distro}\n'
            f'Suites: {codename}\nComponents: stable\nArchitectures: {arch}\n'
            'Signed-By: /etc/apt/keyrings/docker.asc\n')
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', 'docker-ce', 'docker-ce-cli', 'containerd.io',
            'docker-buildx-plugin', 'docker-compose-plugin')
    else:
        raise RuntimeError('Unsupported OS for automatic Docker installation. Install Docker Engine and Compose plugin.')
    run('systemctl', 'enable', '--now', 'docker')
    run('docker', 'info', capture=True)


def check_ports(bind_ip: str) -> None:
    occupied = []
    for port in PORTS:
        with socket.socket() as sock:
            try:
                sock.bind((bind_ip, port))
            except OSError:
                occupied.append(port)
    if occupied:
        raise RuntimeError(f'Host ports in use: {occupied}. Stop the conflicting service or use another host.')


def command(*args: str) -> None:
    run(*args, capture=True)



def client_certificate(certs: Path, secret_dir: Path, tls_password: str) -> None:
    """Make a test-only client identity for mutual DICOM TLS checks."""
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    secret_dir.chmod(0o700)
    key = secret_dir / 'test-client.key'
    csr = secret_dir / 'test-client.csr'
    crt = ROOT / 'client' / 'test-client.crt'
    crt.parent.mkdir(exist_ok=True)
    extension = secret_dir / 'test-client.ext'
    extension.write_text('basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=clientAuth\n')
    command('openssl', 'req', '-new', '-newkey', 'rsa:3072', '-sha256', '-nodes',
            '-keyout', str(key), '-out', str(csr), '-subj', '/CN=DCM4CHEE Test Client')
    command('openssl', 'x509', '-req', '-in', str(csr), '-CA', str(certs / 'ca.crt'),
            '-CAkey', str(certs / 'ca.key'), '-CAcreateserial', '-out', str(crt),
            '-days', '825', '-sha256', '-extfile', str(extension))
    command('openssl', 'pkcs12', '-export', '-inkey', str(key), '-in', str(crt),
            '-certfile', str(certs / 'ca.crt'), '-out', str(secret_dir / 'test-client.p12'),
            '-name', 'test-client', '-passout', f'pass:{tls_password}')
    for path in secret_dir.glob('test-client.*'):
        path.chmod(0o600)
    crt.chmod(0o644)

def certificate(host: str, ip: str, tls_password: str):
    certs = ROOT / 'certs'
    certs.mkdir(mode=0o700, exist_ok=True)
    certs.chmod(0o700)
    try:
        ipaddress.ip_address(host)
        san = f'IP:{host}'
    except ValueError:
        san = f'DNS:{host},IP:{ip}'
    def openssl(*args: str):
        command('openssl', *args)
    openssl('req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes', '-days', '3650',
            '-keyout', str(certs / 'ca.key'), '-out', str(certs / 'ca.crt'),
            '-subj', '/CN=DCM4CHEE Test CA')
    openssl('req', '-new', '-newkey', 'rsa:3072', '-sha256', '-nodes',
            '-keyout', str(certs / 'tls.key'), '-out', str(certs / 'tls.csr'),
            '-subj', f'/CN={host}', '-addext', f'subjectAltName={san}')
    openssl('x509', '-req', '-in', str(certs / 'tls.csr'), '-CA', str(certs / 'ca.crt'),
            '-CAkey', str(certs / 'ca.key'), '-CAcreateserial', '-out', str(certs / 'tls.crt'),
            '-days', '825', '-sha256', '-copy_extensions', 'copy')
    for name in ('keycloak', 'arc'):
        openssl('pkcs12', '-export', '-inkey', str(certs / 'tls.key'), '-in', str(certs / 'tls.crt'),
                '-certfile', str(certs / 'ca.crt'), '-out', str(certs / f'{name}.p12'),
                '-name', name, '-passout', f'pass:{tls_password}')
    command('docker', 'run', '--rm', '-u', f'{os.getuid()}:{os.getgid()}',
            '-v', f'{certs}:/certs:Z', '--entrypoint', '/usr/bin/keytool', IMAGES[0],
            '-importcert', '-noprompt', '-alias', 'test-ca', '-file', '/certs/ca.crt',
            '-keystore', '/certs/ca.p12', '-storetype', 'PKCS12', '-storepass', 'changeit')
    for path in certs.iterdir():
        path.chmod(0o600)
    for path in certs.glob('*.p12'):
        path.chmod(0o644)
    (ROOT / 'client').mkdir(exist_ok=True)
    shutil.copyfile(certs / 'ca.crt', ROOT / 'client' / 'test-ca.crt')
    client_certificate(certs, ROOT / 'secrets', tls_password)



def reuse_certificates(source: Path, host: str, values: dict[str, str]) -> None:
    """Keep a trusted test CA when replacing an installation on the same host."""
    source = source.resolve()
    prior = dict(line.split('=', 1) for line in (source / '.env').read_text().splitlines() if '=' in line)
    if prior.get('PUBLIC_HOST') != host:
        raise RuntimeError('Reused certificates must match the chosen host name.')
    cert_source = source / 'certs'
    required = ('ca.crt', 'ca.key', 'ca.p12', 'arc.p12', 'keycloak.p12', 'tls.crt')
    for name in required:
        if not (cert_source / name).is_file():
            raise RuntimeError(f'Missing certificate in old installation: {name}')
    details = run('openssl', 'x509', '-in', str(cert_source / 'tls.crt'),
                  '-noout', '-ext', 'subjectAltName', capture=True).stdout
    try:
        ipaddress.ip_address(host)
        expected = f'IP Address:{host}'
    except ValueError:
        expected = f'DNS:{host}'
    if expected not in details:
        raise RuntimeError(f'Old server certificate does not contain {expected}.')
    certs = ROOT / 'certs'
    certs.mkdir(mode=0o700, exist_ok=True)
    certs.chmod(0o700)
    for name in required:
        shutil.copy2(cert_source / name, certs / name)
    values['TLS_KEYSTORE_PASSWORD'] = prior['TLS_KEYSTORE_PASSWORD']
    values['EXTRA_CACERTS_PASSWORD'] = prior['EXTRA_CACERTS_PASSWORD']
    for path in certs.iterdir():
        path.chmod(0o600)
    for path in certs.glob('*.p12'):
        path.chmod(0o644)
    (ROOT / 'client').mkdir(exist_ok=True)
    shutil.copyfile(certs / 'ca.crt', ROOT / 'client' / 'test-ca.crt')
    client_certificate(certs, ROOT / 'secrets', values['TLS_KEYSTORE_PASSWORD'])

def load_images(path: Path):
    title(f'Loading offline images from {path}')
    if path.suffix == '.gz':
        with gzip.open(path, 'rb') as source:
            result = subprocess.run(['docker', 'load'], stdin=source, check=False)
    else:
        result = subprocess.run(['docker', 'load', '-i', str(path)], check=False)
    if result.returncode:
        raise RuntimeError('Could not load the image archive.')



def ensure_ui_war():
    war = ROOT / 'build' / 'archive-ui.war'
    if war.is_file() and war.stat().st_size > 1_000_000:
        return
    war.parent.mkdir(parents=True, exist_ok=True)
    container = run('docker', 'create', IMAGES[0], capture=True).stdout.strip()
    try:
        run('docker', 'cp', f'{container}:/docker-entrypoint.d/deployments/dcm4chee-arc-ui2-5.35.1-secure.war', str(war))
    finally:
        run('docker', 'rm', container)

def wait_health(service: str, seconds: int):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        container = run('docker', 'compose', 'ps', '-q', service, capture=True).stdout.strip()
        if container:
            status = run('docker', 'inspect', '--format', '{{.State.Health.Status}}', container,
                         capture=True, check=False).stdout.strip()
            if status == 'healthy':
                print(f'  {service}: healthy', flush=True)
                return
        time.sleep(5)
    raise RuntimeError(f'{service} did not become healthy. Run: docker compose logs {service}')


def wait_arc(seconds: int = 600):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = run('docker', 'compose', 'exec', '-T', 'arc', 'sh', '-c',
                     'grep WFLYSRV0025 /opt/wildfly/standalone/log/server.log | tail -1',
                     capture=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            probe = run('docker', 'compose', 'exec', '-T', 'arc', 'sh', '-c',
                        'curl -k -s -o /dev/null -w %{http_code} https://127.0.0.1:8443/dcm4chee-arc/ui2',
                        capture=True, check=False)
            if probe.stdout.strip() in ('200', '302', '303'):
                return
        time.sleep(5)
    raise RuntimeError('ARC did not start. Check: docker compose logs arc')


def verify_https(host: str, port: int):
    ctx = ssl.create_default_context(cafile=str(ROOT / 'certs' / 'ca.crt'))
    try:
        with urllib.request.urlopen(f'https://{host}:{port}/', context=ctx, timeout=15) as response:
            print(f'  HTTPS {port}: {response.status}', flush=True)
    except urllib.error.HTTPError as error:
        if error.code >= 500:
            raise
        print(f'  HTTPS {port}: {error.code} (service responded)', flush=True)


def write_initial(host: str, ip: str, admin: str, pacs_admin: str, storage: str, reuse_certs: Path | None,
                  cyrillic_ui: bool) -> None:
    secret_dir = ROOT / 'secrets'
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    secret_dir.chmod(0o700)
    if (ENV.exists() or ((ROOT / 'certs').exists() and any((ROOT / 'certs').iterdir()))
            or ((ROOT / 'data').exists() and any((ROOT / 'data').iterdir()))):
        raise RuntimeError('Existing secrets or certificates found without a complete .env. Resolve this partial setup before continuing.')
    values = dict(PUBLIC_HOST=host, PUBLIC_BIND_IP=ip,
                  UI_WAR_PATH='./build/archive-ui-cyrillic.war' if cyrillic_ui else './build/archive-ui.war',
                  MARIADB_ROOT_PASSWORD=secrets.token_hex(32),
                  KEYCLOAK_DB_PASSWORD=secrets.token_hex(32),
                  PACS_DB_PASSWORD=secrets.token_hex(32),
                  KEYCLOAK_ADMIN_USER=admin, KEYCLOAK_ADMIN=admin, PACS_ADMIN_USER=pacs_admin,
                  KEYCLOAK_ADMIN_PASSWORD=secrets.token_hex(32),
                  TLS_KEYSTORE_PASSWORD=secrets.token_hex(32), EXTRA_CACERTS_PASSWORD='changeit')
    values.update(memory_values())
    values.update(storage_values(storage))
    if reuse_certs:
        reuse_certificates(reuse_certs, host, values)
    else:
        certificate(host, ip, values['TLS_KEYSTORE_PASSWORD'])
    ENV.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
    ENV.chmod(0o600)
    creds = secret_dir / 'INITIAL_CREDENTIALS.txt'
    creds.write_text('Initial local credentials (private, mode 600).\n'
                     + '\n'.join(f'{k}={v}' for k, v in values.items()) + '\n'
                     + 'PACS passwords will be added after account setup.\n')
    creds.chmod(0o600)
    for subdir in ('ldap', 'slapd', 'mariadb', 'keycloak', 'postgres', 'wildfly', 'storage/fs1'):
        (ROOT / 'data' / subdir).mkdir(parents=True, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Install secure DCM4CHEE + PostgreSQL with Docker Compose')
    parser.add_argument('--yes', action='store_true', help='accept detected defaults')
    parser.add_argument('--dry-run', action='store_true', help='show choices without changing anything')
    parser.add_argument('--host', help='IP address or DNS name used by browsers')
    parser.add_argument('--bind-ip', help='host IPv4 address for published ports')
    parser.add_argument('--storage', choices=('auto', 'ssd', 'hdd'), default='auto',
                        help='storage profile; auto reads the disk rotational flag')
    parser.add_argument('--kc-admin', default='kcadmin', help='Keycloak master admin name')
    parser.add_argument('--pacs-admin-user', default='admin',
                        help='optional named PACS administrator, e.g. isidora (default: built-in admin)')
    parser.add_argument('--reuse-certs-from', type=Path,
                        help='reuse CA and server certificates from another stack with the same hostname')
    parser.add_argument('--cyrillic-ui', action='store_true',
                        help='compile and enable Serbian Cyrillic alongside Serbian Latin (requires Node image and time)')
    parser.add_argument('--images-archive', type=Path, help='offline docker save archive (.tar or .tar.gz)')
    parser.add_argument('--reconfigure-users', action='store_true', help='rotate initial PACS passwords again')
    args = parser.parse_args()
    if os.geteuid() and not args.dry_run:
        raise RuntimeError('Run as root: sudo python3 deploy.py')
    if ENV.exists():
        current = dict(line.split('=', 1) for line in ENV.read_text().splitlines() if '=' in line)
        host, ip = current['PUBLIC_HOST'], current['PUBLIC_BIND_IP']
        print('Existing installation detected. Data, certificates and passwords will be reused.', flush=True)
    else:
        if not args.dry_run:
            ensure_tools()
        elif not shutil.which('ip') and not args.bind_ip:
            raise RuntimeError('Pass --bind-ip for dry-run when iproute is missing.')
        ip = args.bind_ip or host_ipv4()
        host = args.host or (ip if args.yes or args.dry_run else prompt('Browser IP address or DNS name', ip))
    bind_address = ipaddress.ip_address(ip)
    if bind_address.is_loopback:
        raise RuntimeError('A loopback bind IP cannot be reached by the Docker containers. Choose a host LAN IP.')
    if any(c in host for c in ('/', ':', ' ', '\\')):
        raise RuntimeError('Host must be a plain IPv4 address or DNS name, without scheme or port.')
    try:
        socket.getaddrinfo(host, 8843)
    except socket.gaierror:
        raise RuntimeError(f'{host} does not resolve on this server. Set up DNS/hosts or use the host IP.')
    title('Installation plan')
    print(f'Host: {host}; bind IP: {ip}; project: {ROOT}; ports: {PORTS}', flush=True)
    print(f'RAM profile: {memory_values()}', flush=True)
    print(f'Storage profile: {storage_values(args.storage)}', flush=True)
    if args.dry_run:
        print('Dry run complete. No files or containers were changed.', flush=True)
        return
    title('Prerequisites and images')
    ensure_tools()
    if not ENV.exists():
        check_ports(ip)
    if args.images_archive:
        load_images(args.images_archive)
    for image in IMAGES:
        if run('docker', 'image', 'inspect', image, capture=True, check=False).returncode:
            title(f'Pulling {image}')
            run('docker', 'pull', image)
    ensure_ui_war()
    if not ENV.exists():
        if args.cyrillic_ui:
            title('Compiling Serbian Cyrillic UI')
            run(sys.executable, 'scripts/compile-cyrillic-ui.py')
        active_war = ROOT / ('build/archive-ui-cyrillic.war' if args.cyrillic_ui else 'build/archive-ui.war')
    else:
        current = dict(line.split('=', 1) for line in ENV.read_text().splitlines() if '=' in line)
        active_war = ROOT / current.get('UI_WAR_PATH', './build/archive-ui.war')
    run(sys.executable, 'scripts/set-default-ui-language.py', str(active_war))
    if not ENV.exists():
        title('Credentials and TLS certificates')
        write_initial(host, ip, args.kc_admin, args.pacs_admin_user, args.storage, args.reuse_certs_from,
                      args.cyrillic_ui)
    title('Starting services')
    run('docker', 'compose', 'up', '-d')
    for service, seconds in (('ldap', 240), ('mariadb', 240), ('db', 600), ('keycloak', 600)):
        wait_health(service, seconds)
    wait_arc()
    marker = ROOT / 'secrets' / 'configured'
    if not marker.exists() or args.reconfigure_users:
        title('Configuring accounts, browser callbacks and DICOM TLS')
        run(sys.executable, 'scripts/configure-users.py')
        run(sys.executable, 'scripts/apply-ldap-config.py')
        run('docker', 'compose', 'restart', 'arc')
        wait_arc()
        marker.write_text('Initial account and TLS setup completed.\n')
        marker.chmod(0o600)
    else:
        print('Accounts already configured; passwords left unchanged.', flush=True)
    title('Final checks')
    verify_https(host, 8843)
    verify_https(host, 8443)
    run('docker', 'compose', 'exec', '-T', 'db', 'psql', '-U', 'pacs', '-d', 'pacsdb',
        '-Atc', 'SELECT current_setting(\'shared_buffers\')')
    print(f'''\nPACS UI: https://{host}:8443/dcm4chee-arc/ui2
Keycloak: https://{host}:8843/admin/
WildFly: https://{host}:9993/console/index.html
DICOM: {host}:11112 (AE DCM4CHEE)
Public test CA: {ROOT}/client/test-ca.crt
Test DICOM TLS identity: {ROOT}/secrets/test-client.p12
Passwords: {ROOT}/secrets/INITIAL_CREDENTIALS.txt (mode 600)
Data: {ROOT}/data (keep this folder on the intended storage disk)
''', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
