# Bidrag til Mundsen Agent

Alle bidrag må bevare skillet mellom plattformkode, personlig instans og
runtime.

## Før du endrer kode

1. Beskriv brukerbehovet og hvilken arkitekturgrense som berøres.
2. Klassifiser funksjonen som kjerne, valgfri modul eller personlig
   instansfunksjon.
3. Dokumenter nye dataflyter og godkjenningspunkter.
4. Bruk oppdiktede navn og data i eksempler og tester.

Ikke kopier hele mapper eller Git-historikk fra et personlig assistentrepo.
Gjenbruk skal skje fil for fil etter gjennomgang.

## Ferdigdefinisjon

En endring er ikke ferdig før den har:

- tester som kjører med midlertidig hjem, config, state og cache
- oppdatert relevant dokumentasjon
- oppføring i `CHANGELOG.md`
- oppdatert eksempelkonfigurasjon ved konfigurasjonsendringer
- migreringsnotat ved skjema- eller kompatibilitetsendringer
- vurdering av `PRIVACY.md` og `SECURITY.md`
- bestått secretskann, persondataskann og manuell diffgjennomgang

## Kodekrav

- Python-kode skal støtte den dokumenterte referanseplattformen.
- Bruk standardbiblioteket når det er rimelig.
- Absolutte brukerstier, private domener, interne IP-er og ekte identiteter er
  forbudt.
- Alle runtime-stier skal komme fra en sentral path-konfigurasjon.
- Alle eksterne integrasjoner skal være av som standard.
- Feil til brukeren skal være korte og saniterte. Teknisk detalj kan logges
  lokalt uten samtaleinnhold.
- Tester skal ikke lese eller skrive faktisk hjemmekatalog.

## Commit og historikk

- Bruk små, beskrivende commits.
- Ikke legg credential i en remote-URL.
- Ikke commit `.env`, secrets, personlig instans, runtime-state, logger,
  vedlegg, sikkerhetskopier eller editor-cache.
- Ikke bruk historikkimport eller subtree fra et personlig repo.
- Hvis sensitive data blir committed, stopp og behandle det som en hendelse.

## Release

En release krever:

- alle tester i isolert miljø
- CI på støttede macOS-/Linux- og Python-matriser
- samsvar mellom changelog og manifest
- verifiserte checksums
- test av fersk installasjon, godkjent oppgradering, avvist oppgradering og
  rollback
- kontroll av at personlige filer er byte-identiske etter oppgradering
- secrets- og persondataskann av hele Git-historikken
- GitHub release immutability aktivert og verifisert av releaseworkflowen
- manuell sikkerhets- og personverngjennomgang

Første pilot skal være frivillig og isolert fra andre personlige
assistentinstallasjoner.

Kjør arbeidskopiskannen på hver endring:

```sh
python3 installer/privacy-scan.py
```

Før tagg må også hele historikken passere:

```sh
python3 installer/privacy-scan.py --history
```
