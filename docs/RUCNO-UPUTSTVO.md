# Ručna instalacija dcm4chee sa PostgreSQL-om

Ovo je ručni postupak za isti secure Docker stack koji postavlja `deploy.py`. Komande izvršavaj u root shell-u (`sudo -i`) unutar direktorijuma projekta. Piše za Linux administratora koji želi da vidi i izvrši svaki korak. Za najjednostavniju instalaciju koristi `sudo python3 deploy.py`. Primeri ovde koriste IP adresu `192.0.2.10`; zameni je stvarnom adresom servera pre izvršavanja.

Ako na istom hostu menjaš raniju instalaciju i klijenti već veruju njenom testnom CA sertifikatu, za automatsku instalaciju koristi `--host IME --bind-ip ADRESA --reuse-certs-from /putanja/do/stare/instalacije`. Ime mora biti isto kao u staroj `.env` datoteci, a stari serverski sertifikat mora sadržati to ime u SAN polju. Stara instalacija mora biti zaustavljena kako portovi ne bi bili zauzeti. Opcija `--pacs-admin-user isidora` stvara dodatnog PACS administratora; njegove lozinke će se pojaviti u privatnom fajlu sa kredencijalima.

Opcija `--cyrillic-ui` pri prvom podizanju preuzima izvorni UI za izdanje 5.35.1, gradi poseban Angular paket i omogućava srpsku ćirilicu pored latinice. Za ovaj korak treba internet, prostor na disku i nekoliko minuta. Gotov WAR ostaje u `build/archive-ui-cyrillic.war`. Ako postupak radiš ručno, posle izdvajanja izvornog WAR-a pokreni `python3 scripts/compile-cyrillic-ui.py`, postavi `UI_WAR_PATH=./build/archive-ui-cyrillic.war` u `.env`, pa podigni servise i primeni LDAP podešavanja. Samo preslovljavanje JSON datoteka bez kompajliranja Angular paketa ne prevodi glavni meni.

## 1. Priprema mašine

Odaberi direktorijum na disku gde će ostati baze i studije. Projekat čuva sve trajne podatke u `data/` ispod tog direktorijuma. Proveri kapacitet:

```sh
df -h .
free -h
```

Preporučeno je najmanje 8 GiB RAM-a. Na mašini sa 5 do 7 GiB koristi mali profil iz `.env.example` i ne pokreći druge velike servise. Ako već rade WildFly, slapd ili MySQL i drže potrebne portove, prvo ih zaustavi ili koristi drugu mašinu. Ne briši njihove stare podatke dok nova instalacija nije potvrđena.

Instaliraj Docker Engine i Compose plugin prema [zvaničnom Docker uputstvu](https://docs.docker.com/engine/install/). Na AlmaLinux/Rocky Linux mašini sa internetom:

```sh
sudo dnf -y install dnf-plugins-core openssl python3 iproute
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
docker compose version
```

Na AlmaLinux 10 sa novim `dnf`, ako `--add-repo` nije prihvaćen, koristi `sudo dnf config-manager addrepo --from-repofile https://download.docker.com/linux/centos/docker-ce.repo`. Za server bez interneta prenesi Docker RPM pakete i pet image-a sa druge mašine. Image arhiva može se učitati naredbom `gzip -dc images.tar.gz | sudo docker load`. Nazivi i verzije image-a su u `compose.yaml`.

## 2. Izbor adrese i portova

Unesi jednu adresu koju browser klijenata zaista mogu dosegnuti. Najjednostavnija je IP adresa servera. U primeru:

```sh
export PACS_HOST=192.0.2.10
export PACS_BIND_IP=192.0.2.10
```

Ako koristiš DNS ime, ono mora da se razrešava i na serveru i kod korisnika. Za browser trebaju TCP 8443 i 8843. WildFly konzola je na 9993, namenjena administratorima. DICOM je na 11112 bez TLS-a ili 2762 sa obaveznim klijentskim sertifikatom. Proveri da portove ne koristi drugi program.

## 3. Tajne i testni sertifikat

Napravi direktorijume i kopiju `.env.example`. Ne koristi tekst `REPLACE_WITH_RANDOM_VALUE` kao stvarnu lozinku.

```sh
umask 077
mkdir -p certs client secrets data/storage/fs1 build
cp .env.example .env
chmod 600 .env
```

U `.env` postavi `PUBLIC_HOST` i `PUBLIC_BIND_IP` na izabranu adresu. Za svaku lozinku generiši zasebnu vrednost, na primer `openssl rand -hex 32`. Polja `TLS_KEYSTORE_PASSWORD` i `EXTRA_CACERTS_PASSWORD` zapamti tačno, jer se koriste pri stvaranju PKCS12 datoteka. PostgreSQL vrednosti iz primera prilagodi RAM-u: `PG_SHARED_BUFFERS` mora biti znatno manji od `PG_MEMORY_LIMIT` jer sesije i drugi procesi koriste dodatnu memoriju. [Objašnjenje PostgreSQL memorije](https://www.postgresql.org/docs/18/runtime-config-resource.html).

Napravi testni CA i serverski sertifikat. Zameni `IP:` sa `DNS:` ako si izabrao DNS ime:

```sh
cd certs
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
  -keyout ca.key -out ca.crt -subj '/CN=DCM4CHEE Test CA'
openssl req -new -newkey rsa:3072 -sha256 -nodes \
  -keyout tls.key -out tls.csr -subj "/CN=$PACS_HOST" \
  -addext "subjectAltName=IP:$PACS_HOST"
openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out tls.crt -days 825 -sha256 -copy_extensions copy
cd ..
```

Iz `.env` pročitaj `TLS_KEYSTORE_PASSWORD` i njime izvozi `certs/arc.p12` i `certs/keycloak.p12`:

```sh
set -a; . ./.env; set +a
for name in arc keycloak; do
  openssl pkcs12 -export -inkey certs/tls.key -in certs/tls.crt \
    -certfile certs/ca.crt -out "certs/$name.p12" -name "$name" \
    -passout "pass:$TLS_KEYSTORE_PASSWORD"
done
docker run --rm -v "$PWD/certs:/certs:Z" --entrypoint /usr/bin/keytool \
  dcm4che/dcm4chee-arc-psql:5.35.1-secure \
  -importcert -noprompt -alias test-ca -file /certs/ca.crt \
  -keystore /certs/ca.p12 -storetype PKCS12 \
  -storepass "$EXTRA_CACERTS_PASSWORD"
cp certs/ca.crt client/test-ca.crt
chmod 600 certs/*
chmod 644 certs/*.p12 client/test-ca.crt
```

CA privatni ključ, `.env` i PACS lozinke čuvaj samo na serveru. Korisniku se prenosi javni `client/test-ca.crt`, koji se instalira u pouzdane CA sertifikate njegovog računara. Testni CA nije zamena za sertifikat organizacije.

## 4. Podizanje pet servisa

```sh
docker compose config --quiet
docker compose up -d
docker compose ps
```

Sačekaj da `ldap`, `mariadb`, `db` i `keycloak` budu `healthy`, zatim u ARC logu proveri `WFLYSRV0025`:

```sh
docker compose exec -T arc sh -c 'grep WFLYSRV0025 /opt/wildfly/standalone/log/server.log | tail -1'
```

Ako se neki kontejner podigne pa još nije spreman, sačekaj. Prvo stvaranje baza traje duže. Za grešku pogledaj `docker compose logs --tail=100 IME_SERVISA`.

## 5. Browser povratne adrese, nalozi i jezici

Skripti za birač jezika treba izvorni UI WAR iz zvaničnog image-a. Ovo samo pravi lokalnu kopiju za čitanje zastavica, ne menja program:

```sh
cid=$(docker create dcm4che/dcm4chee-arc-psql:5.35.1-secure)
docker cp "$cid:/docker-entrypoint.d/deployments/dcm4chee-arc-ui2-5.35.1-secure.war" build/archive-ui.war
docker rm "$cid"
```

Zatim izvrši početno podešavanje:

```sh
python3 scripts/configure-users.py
python3 scripts/apply-ldap-config.py
docker compose restart arc
```

`configure-users.py` registruje browser adresu u Keycloak-u, omogućava profil korisnika, dodeljuje `account` uloge `view-profile` i `manage-account`, menja lozinke početnih naloga `root`, `admin`, `user` i proverava OIDC prijavu. Svako novo pokretanje te skripte ponovo menja sve te lozinke. Rezultat je u `secrets/INITIAL_CREDENTIALS.txt` sa dozvolama 600. `apply-ldap-config.py` podešava DICOM TLS, WildFly konzolu i UI birač za English i Srpski (latinica), a uz `--cyrillic-ui` i za Српски (ћирилица).

Pre podizanja servisa automatski postupak izvršava `python3 scripts/set-default-ui-language.py build/archive-ui.war` (ili ćirilični WAR ako je uključen). Time srpska latinica postaje početni jezik za korisnika bez sačuvanog izbora. Početnim nalozima skripta za korisnike postavlja `locale=sr` samo ako atribut još nije definisan. Promena jezika određenog postojećeg naloga bez promene lozinke radi se komandama, na primer `python3 scripts/set-pacs-language.py isidora sr` ili `python3 scripts/set-pacs-language.py isidora sr-Cyrl`.

## 6. Proba iz browsera i DICOM klijenta

Otvori `https://ADRESA:8443/dcm4chee-arc/ui2`. Prijavi se nalogom `admin` iz fajla sa kredencijalima. U zaglavlju se prikazuje korisničko ime i meni za odjavu. Klikni naziv jezika i izaberi **Srpski (latinica)**. Na stranici Studije izaberi servis `DCM4CHEE`, klikni **PODNESI** i potvrdi pretragu bez filtera. Nova prazna arhiva može vratiti nula studija.

Za DICOM klijent postavi Called AE Title `DCM4CHEE`, adresu servera i port 11112. Pošalji samo sintetičku probnu studiju, zatim proveri da se vidi u UI-ju i da raste sadržaj `data/storage/fs1`. Port 2762 traži TLS klijentski sertifikat izdat od CA kome arhiva veruje. `deploy.py` za probu pravi `secrets/test-client.p12`; u stvarnoj ustanovi svaki modalitet treba svoj sertifikat.

## 7. Rad i backup

```sh
docker compose ps
docker compose logs --tail=100 arc db keycloak
docker compose exec -T db psql -U pacs -d pacsdb -Atc 'SHOW shared_buffers; SHOW join_collapse_limit;'
du -sh data/storage/fs1
docker compose down
```

`down` čuva datoteke u `data/`; ne koristi `down -v` i ne briši `data/` ako studije treba da ostanu. Za backup sačuvaj `data/`, `.env`, `certs/`, `secrets/` i `compose.yaml` zajedno, van servera, uz ograničen pristup. Proveri vraćanje na odvojenoj testnoj instalaciji pre rada sa stvarnim studijama.
