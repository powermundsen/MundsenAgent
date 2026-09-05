# Releaseprosess

Mundsen Agent installeres i produksjon bare fra en versjonert, immutable GitHub
Release. `main` og løse Actions-artifacts er aldri produksjonskilder.

## Engangsoppsett på GitHub

1. Aktiver **Settings → Releases → Enable release immutability**.
2. Publiser bare den rene, skannede Mundsen Agent-historikken. Personlig
   kildehistorikk skal forbli i et separat privat arkiv.

Releaseworkflowen spør GitHubs repository-endepunkt direkte og feiler lukket
dersom release immutability ikke faktisk er aktivert.

## Kandidatport

Før tagg:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests installer/privacy-scan.py
bash -n installer/install.sh installer/build-release.sh
python3 installer/privacy-scan.py
python3 installer/privacy-scan.py --history
git diff --check
```

Arbeidskopiskannen inkluderer både tracked og untracked filer. Historikkskannen
går gjennom alle nåbare blobs og sperrer kjente private identifikatorer,
credentialmønstre, private nett og absolutte brukerstier. Automatisk skann må
følges av manuell gjennomgang.

## Publisering

1. Oppdater packageversjon, changelog og standardversjon i installeren.
2. Bygg lokalt med `installer/build-release.sh <versjon> dist`.
3. Verifiser `release-manifest.json`, `SHA256SUMS` og installerpakkens checksum.
4. Kjør fersk installasjon, Telegram-dialog, update, rollback og avinstallasjon
   i en disponibel OS-bruker eller VM uten tilgang til en eksisterende agent.
5. Opprett og push en annotert tagg `v<versjon>`.
6. Actions oppretter en draft, laster opp alle assets og publiserer til slutt.
   GitHub låser da tagg og assets og genererer release-attestasjon.

En release skal ikke publiseres dersom den isolerte installasjonsreisen,
historikkskannen eller immutable-release-kontrollen mangler.

## Artefakter

Hver release inneholder:

- `mundsen-agent-<versjon>.tar.gz`
- `release-manifest.json`
- `MundsenAgent-<versjon>-installer.sh`
- `MundsenAgent-<versjon>-installer.sh.sha256`
- `SHA256SUMS`

Releasemanifestet er maskinkontrakten for updateren. `SHA256SUMS` er den
menneskelesbare kontrollen av arkiv og installer.
