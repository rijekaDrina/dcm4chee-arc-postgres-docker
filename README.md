# dcm4chee-arc PostgreSQL Docker installer

Secure dcm4chee-arc 5.35.1 with PostgreSQL 18, Keycloak 25, LDAP and HTTPS. The CLI wizard creates private credentials and a test CA, starts the five Docker services, configures initial users, and prints the URLs to open.

This project is intended for test deployments. Use organization managed certificates, DNS, access rules and backups before storing real patient data.

## Quick start

Use a Linux host with at least 5 GiB RAM and preferably 8 GiB or more. Put the project directory on the filesystem where you want to store studies, because all persistent data is under `data/` in that directory. You need root or sudo and internet access to pull images and install Docker if missing.

```sh
git clone https://github.com/rijekaDrina/dcm4chee-arc-postgres-docker.git
cd dcm4chee-arc-postgres-docker
python3 deploy.py --dry-run
sudo python3 deploy.py
```

The wizard proposes the host's IPv4 address. Press Enter to use it. To skip prompts:

```sh
sudo python3 deploy.py --yes
```

To create a named PACS administrator in addition to the built-in accounts, add `--pacs-admin-user NAME`. To replace another installation while keeping clients' existing trusted CA, add `--reuse-certs-from /path/to/old/stack`; the old certificate must cover the same hostname. Add `--cyrillic-ui` to build and enable **Srpski (latinica)** and **Српски (ћирилица)** in the UI picker. This first build downloads the matching upstream UI source and Node image and can take several minutes.

On a host that cannot reach GitHub, place a verified `build/archive-ui-cyrillic.war` from the matching release in the project before running with `--cyrillic-ui`. The wizard validates the archive and uses it without downloading the UI source.

If Docker is already installed but the machine has no internet, copy a `docker save` archive of the five pinned images to the server and run:

```sh
sudo python3 deploy.py --yes --images-archive /path/to/images.tar.gz
```

The wizard refuses to overwrite an existing `.env`, certificate or data folder. Run it again to resume a failed deployment; it leaves completed user setup and passwords unchanged. Use `--reconfigure-users` only when you intend to rotate all three initial PACS passwords.

## What it installs

| Service | Image | Purpose |
| --- | --- | --- |
| `arc` | `dcm4che/dcm4chee-arc-psql:5.35.1-secure` | PACS, UI, DICOMweb |
| `db` | `dcm4che/postgres-dcm4chee:18.3-35` | PACS database |
| `ldap` | `dcm4che/slapd-dcm4chee:2.6.13-35.1` | Archive configuration and accounts |
| `keycloak` | `dcm4che/keycloak:25.0.6` | Secure login |
| `mariadb` | `mariadb:10.11` | Keycloak database |

The archive uses the official PostgreSQL secure image; no custom PACS image is built. The wizard detects host RAM and selects conservative initial PostgreSQL memory, connection and WAL settings. It explicitly sets `join_collapse_limit=16` and `from_collapse_limit=16` because the upstream PostgreSQL 18 image initializer still targets the pre-18 `postgresql.conf` path. It checks the disk's rotational flag to choose initial I/O cost settings; on virtual machines, where hypervisors usually report SSD/SAN disks as rotational, it assumes SSD (pass `--storage hdd` if the datastore really is spinning disks). Container memory limits are sized so the host keeps a reserve: on a 32 GB host all limits add up to about 23 GB. Container logs are capped at 3 x 10 MB per service. These are starting values and should be reviewed with actual workload measurements. [PostgreSQL memory guidance](https://www.postgresql.org/docs/18/runtime-config-resource.html).

Study files use `data/storage/fs1` on the host, mapped to `/storage/fs1` in ARC. Put this project on the intended data disk before deployment. Do not change the storage path in the UI unless you understand the archive's storage configuration. The wizard sets a free-space reserve on `fs1` (`dcmStorageThreshold`, 2% of the disk, at most 50 GB): when it is reached, the archive rejects new objects instead of filling the disk to 100%. If `data/` or `data/storage` is a separate mount, the wizard makes Docker wait for those mounts at boot (`/etc/systemd/system/docker.service.d/dcm4chee-mounts.conf`).

## After installation

| What | Address |
| --- | --- |
| PACS UI | `https://<host>:8443/dcm4chee-arc/ui2` |
| Keycloak administration | `https://<host>:8843/admin/` |
| WildFly console | `https://<host>:9993/console/index.html` |
| DICOM C-ECHO and C-STORE | `<host>:11112`, called AE `DCM4CHEE` |
| DICOM TLS, client certificate required | `<host>:2762` |
| DICOMweb | `https://<host>:8443/dcm4chee-arc/aets/DCM4CHEE/rs` |

The public test CA is `client/test-ca.crt`. A test-only DICOM TLS client identity is generated as `secrets/test-client.p12` (private, mode 600), with public certificate `client/test-client.crt`. Give each real modality its own managed certificate instead of copying this test identity. Install it in the client computer's trusted root certificate store before opening HTTPS. The initial passwords are in `secrets/INITIAL_CREDENTIALS.txt` with mode 600. `kcadmin` belongs to Keycloak's `master` realm; `root`, `admin` and `user` belong to the PACS `dcm4che` realm. Use `admin` for the PACS UI and reserve `root` for recovery. If you used `--pacs-admin-user`, that named account has administrator roles and is listed in the same credentials file.

The secure UI includes the upstream Serbian Latin translation. The installer enables **English** and **Srpski (latinica)** in the UI language picker and opens in Serbian Latin for users without a saved preference. With `--cyrillic-ui`, it compiles a separate Angular bundle from the reviewed `locales/sr-Cyrl` resources and adds **Српски (ћирилица)**. Click the current language in the header to switch; the choice is saved in the account profile. The Cyrillic resources are also proposed upstream in [dcm4chee-arc-lang PR #94](https://github.com/dcm4che/dcm4chee-arc-lang/pull/94).

## Network access

The wizard publishes ports on the chosen host IPv4 address. Client computers still need a route and firewall permission to reach that address. If you use a DNS name with `--host`, it must resolve to the same host on both the server and every browser client. Using the IP address is simplest for a test deployment.

### Docker address pools

Docker's default address pools cover 172.16.0.0/12 and 192.168.0.0/16 — ranges many hospital networks use for their own subnets. When a Docker network lands on the same range as the LAN, the server treats that range as directly connected and answers LAN clients through the Docker bridge instead of the gateway: from the moment Docker starts, clients can no longer reach the server, and the OIDC redirects fail with it.

The wizard prevents this on every deployment: it picks a /16 free of host LAN overlaps (default `10.234.0.0/16`, then the next free 10.x range, or `--docker-subnet` to choose explicitly), writes it to `/etc/docker/daemon.json` as `default-address-pools` plus a `bip` for the default bridge, and restarts Docker when the configuration changed. If an existing compose network still sits on LAN space, it is recreated with the dedicated pool. On a machine deployed before this setting, the same repair by hand:

```sh
cd /srv/dcm4chee/dcm4chee-arc-postgres-docker
sudo docker compose down
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "bip": "10.234.255.1/24",
  "default-address-pools": [{ "base": "10.234.0.0/16", "size": 24 }]
}
EOF
sudo systemctl restart docker
sudo docker compose up -d
```

Use a range that is free in the local network plan; only Docker routes inside it.

Open 8443 and 8843 to intended browser clients. Limit 9993 to administrators. Open 11112 or 2762 only to authorized DICOM clients. Open HL7 ports 2575 and 12575 only where needed. Do not expose this test stack directly to the public internet.

## Daily operations

```sh
sudo docker compose ps
sudo docker compose logs --tail=100 arc keycloak db
sudo docker compose up -d
sudo docker compose down
sudo docker compose exec db psql -U pacs -d pacsdb
```

`docker compose down` preserves data under `data/`. Do not use `down -v` or delete `data/` unless you intend to erase the installation. Back up the entire `data/` directory together with `.env`, `certs/`, `secrets/` and `compose.yaml`. Verify that a backup can be restored before using real studies.

See the detailed [manual installation guide in Serbian](docs/RUCNO-UPUTSTVO.md) for every command used by the wizard.

## Manual setup and troubleshooting

The wizard uses [compose.yaml](compose.yaml) and the scripts under [scripts](scripts). To operate manually, install Docker Engine and its Compose plugin, generate unique values for every secret in `.env.example`, create an HTTPS certificate with an IP or DNS SAN, export `arc.p12`, `keycloak.p12` and `ca.p12`, then run `docker compose up -d`. Once all services are healthy, run `python3 scripts/configure-users.py` and `python3 scripts/apply-ldap-config.py`, restart ARC, and verify login, QIDO and C-ECHO. The wizard performs these steps and is the recommended first install route.

- Server unreachable from LAN clients since Docker was installed: a Docker network or the default bridge overlaps the LAN subnet. On the server check `ip route get <LAN-client-IP>` — it must leave through the LAN interface, not through `br-…` or `docker0`. Apply the address pool repair from "Network access".
- `Invalid parameter: redirect_uri`: use the same host string entered in the wizard, and check client `dcm4chee-arc-ui` in Keycloak.
- Many 401 errors or no logout: check the `account` client roles `view-profile` and `manage-account` for the PACS user. `configure-users.py` assigns them to the initial accounts and rotates passwords if run again.
- No studies found: choose web application service `DCM4CHEE` on the Studies page and submit. An empty new archive should return no studies.
- Archive does not start: `docker compose logs arc`, then `docker compose exec arc tail -100 /opt/wildfly/standalone/log/server.log`.
- Studies rejected and storage marked full: free space dropped below `dcmStorageThreshold`; add disk space or archive old studies.
- PostgreSQL unhealthy: `docker compose logs db`; check disk space and that its memory limit is greater than `shared_buffers`.

[Docker Engine installation](https://docs.docker.com/engine/install/) · [Compose plugin installation](https://docs.docker.com/compose/install/linux/) · [dcm4chee secure archive guide](https://github.com/dcm4che/dcm4chee-arc-light/wiki/Run-secured-archive-services-on-a-single-host)
