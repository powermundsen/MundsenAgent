# Oppgradering og rollback

## Implementasjonsstatus

Utviklingskandidaten kan oppdage siste stabile GitHub Release, hente og
validere manifestet, laste ned arkivet med en eksplisitt kommando, verifisere
SHA-256, bytte `current` atomisk og rulle tilbake. Den tar backup og hash av
beskyttede filer, krever grønn health check før endring, verifiserer aktiv
release etter restart og går automatisk tilbake ved feil.

## Prinsipp

Mundsen Agent oppgraderes bare fra en versjonert GitHub Release og bare etter
eksplisitt godkjenning. `main`, tilfeldige commits og automatisk
selvoppdatering er ikke gyldige produksjonskilder.

## Ukentlig sjekk

Den lokale bridgen kan én gang i uken:

1. Hente siste release-metadata.
2. Sammenligne versjon med aktiv release.
3. Verifisere at manifestformatet støttes.
4. Presentere endringer, berørte moduler, sikkerhetsmerknader,
   personvernkonsekvenser og migreringsbehov.
5. Sende ett varsel og vente på en separat, eksplisitt installasjonskommando.

Sjekken skal ikke sende lokal versjon, maskinidentitet, modulvalg eller
bruksdata til Mundsen Agent.

Manuell kontroll og nedlasting:

```sh
mundsen-agent update check
mundsen-agent update download
mundsen-agent update install
```

`check` henter bare release-JSON og manifest. `download` henter og verifiserer
arkivet uten å aktivere det. `install` viser versjon, berørte moduler,
persondataeffekt og migreringer før den bruker den eksisterende health-,
backup- og rollbackflyten. Uten `--yes` kreves lokal bekreftelse.

## Releasemanifest

`release-manifest.json` skal minst inneholde:

```json
{
  "manifest_version": 1,
  "version": "0.3.0",
  "minimum_instance_schema": 1,
  "maximum_instance_schema": 1,
  "files": [
    {
      "path": "mundsen-agent.tar.gz",
      "sha256": "<sha256>"
    }
  ],
  "modules_changed": ["telegram", "router"],
  "personal_data_impact": "none",
  "migrations": [],
  "rollback_supported": true
}
```

Manifest og publiserte filer skal hentes fra samme release. Checksums
verifiseres før noe installeres.

## Godkjent oppgradering

Etter godkjenning gjør den lokale updateren:

1. Lese manifestets versjon, berørte moduler, datakonsekvens og migreringer.
2. Kreve eksplisitt godkjenning.
3. Kjøre health check mot aktiv release før noe endres.
4. Ta backup av `instance.yaml`, runtimekonfigurasjon og hashoversikt.
5. Registrere hash av alle personlige filer og secrets som ikke skal endres.
6. Verifisere lokalt manifest, arkivchecksum og skjemakompatibilitet.
7. Avvise releasen dersom den deklarerer en migrering. 0.3.0 har ingen
   migreringsmotor.
8. Installere til `<Mundsen Agent>/releases/<versjon>` og lagre et privat
   filintegritetsmanifest.
9. Bytte `<Mundsen Agent>/current` atomisk og restarte tjenesten når den er installert.
10. Kjøre en ny health check og verifisere at beskyttede filer er uendret.

Ved feil før atomisk bytte forblir gammel release aktiv. Ved feil etter bytte
aktiveres og helsesjekkes den forrige releasen automatisk. Backupen beholdes
for manuell kontroll. Siden migreringer avvises, kan 0.3.0 aldri delvis endre
personlige data som del av en oppgradering.

## Avvist eller utsatt oppgradering

Et nei eller manglende svar skal ikke endre runtime. Updateren kan lagre at
versjonen er sett, men skal ikke skjule en senere sikkerhetsoppdatering.

## Rollback

Rollback velger en oppgitt eller forrige beholdt release, kontrollerer
filintegritet, rollbackflagg og kompatibilitet med nåværende instansskjema.
Før endringen kjøres health check, beskyttede filer hashes og en ny backup tas.
Etter atomisk bytte restartes tjenesten og samme kontroller gjentas.

Rollback krever eksplisitt godkjenning med mindre den nye releasen feiler sin
første health check umiddelbart etter en allerede godkjent oppgradering. I det
tilfellet kan updateren gå automatisk tilbake til den versjonen som nettopp var
aktiv.

## Beskyttede filer

Følgende ligger aldri i en releasepakke og skal ikke overskrives:

- `AGENTS.md` og `CLAUDE.md`
- `MEMORY.md`, `TODO.md` og `reminders.md`
- alt under instansens `memory/` og private `skills/`
- secrets, sesjoner, vedlegg og samtalelogger

En release kan levere nye maler ved siden av eksisterende filer. Brukeren kan
senere godkjenne en konkret, synlig sammenslåing.

## GitHub-tilgang

Det offentlige, saniterte repoet trenger ikke token for release discovery eller
nedlasting. Repoeierens PAT eller andre delte credentials skal aldri
installeres hos en bruker.

Ved en privat fork kan brukerens egen fine-grained credential legges som
`MUNDSEN_GITHUB_TOKEN` i `config/secrets.env`. Et personlig eid privat repo gir
imidlertid collaborator-skrivetilgang, ikke en egen read-only collaboratorrolle.

## Bidrag fra brukere

Brukere skal kunne foreslå endringer, men aldri pushe direkte til `main`.
Hold runtime-tokenet read-only (se over) og la bidrag gå gjennom en egen,
eksplisitt godkjenning:

- **Offentlig repo:** brukeren forker og åpner pull request uten skrivetilgang
  til originalens `main`.
- **Privat fork:** inviter bare navngitte GitHub-kontoer, beskytt
  `main` så langt kontoplanen tillater, og bruk feature branch + pull request.
  Dette gir fortsatt bredere tilgang enn ønsket sluttmodell.
- **Vil/kan de ikke bruke GitHub:** de sender en patch (`git format-patch` /
  diff) som eieren gjennomgår og committer selv.

I alle tilfeller er det eieren som merger inn i Mundsen Agent. Ingen bruker får en
delt administratorcredential.

## Oppbevaring

Minst aktiv og forrige fungerende release beholdes. Backups får tidsstempel,
versjon og skjema, og slettes bare etter dokumentert retention-policy eller
eksplisitt brukerhandling.
