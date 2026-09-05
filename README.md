# Mundsen Agent

Mundsen Agent er et gjenbrukbart byggesett for personlige assistenter basert
på Claude Code og Codex CLI. Plattformen leverer den delte koden. Hver bruker
eier sin egen persona, konfigurasjon, hukommelse, samtalehistorikk og
hemmeligheter lokalt.

> **Status:** Utviklingskandidaten har nå installer, førstegangsoppsett,
> Telegram-bridge, Claude/Codex-routing, bakgrunnstjeneste, health check og
> lokal rollback. CI, releasebygging, kontrollert update discovery, delte
> Agent Skills, lokal diagnostikk og den valgfrie helsemodulen er implementert.
> Kandidaten er ikke en støttet utgivelse før en fersk installasjon er
> verifisert i en separat OS-bruker eller VM.

## Mål

En Mundsen Agent-instans skal kunne:

- føre Telegram-dialog gjennom Claude Code og Codex CLI
- velge agent, håndtere fallback og bevare sesjonskontinuitet
- lagre profil, åpne tråder, påminnelser og arbeidsminne lokalt
- stoppe aktive jobber og starte en ny samtale på en kontrollert måte
- oppgraderes og rulles tilbake uten å overskrive personlige filer
- bruke valgfrie moduler bare når brukeren har aktivert og konfigurert dem

Mundsen Agent skal ikke inneholde ekte brukeres data. Plattformen har ingen sentral
minnetjeneste, ingen telemetri og ingen avhengighet til API-er som faktureres
per kall.

## Arkitektur

Mundsen Agent skiller strengt mellom tre lag:

1. **Kjerne:** versjonert bridge, routing, runtime og oppgraderingskode.
2. **Personlig instans:** brukerens instruksjoner, profil, minne, påminnelser,
   modulvalg og lokale skills.
3. **Runtime og hemmeligheter:** sesjoner, token, vedlegg, kø, logger og
   autentisering utenfor Git.

Kjernen kan lese den personlige instansen under kjøring, men en oppgradering
kan aldri skrive over personlige filer uten en eksplisitt migrering som
brukeren først har godkjent. Se [arkitekturen](docs/architecture.md).

## Første referanseplattform

Første testede installasjonsvei blir native macOS på både eldre Intel-maskiner
og Apple Silicon. Mundsen Agent kjører bridge, filer og CLI-prosesser lokalt, mens
modellene brukes gjennom Claude Code- og Codex CLI-abonnementene. Det krever
ikke Apple Silicon, lokal modell eller GPU.

Installeren krever:

- macOS eller Linux med `curl`, `tar` og minst 1,5 GiB ledig plass
- tilgang til Mundsen Agent-kilden eller en versjonert installasjonspakke
- Claude- og ChatGPT-abonnement for de agentene brukeren vil aktivere
- LaunchAgent på macOS eller systemd-brukertjeneste på Linux
- Telegram som eneste påkrevde chatmodul

Python 3.12, Claude Code og Codex CLI installeres under valgt Mundsen Agent-katalog.
De offisielle CLI-installeringene kjøres med separat hjemmekatalog og et tomt,
allowlistet miljø. Innlogging skjer interaktivt etterpå.

En egen lokal macOS-bruker anbefales for filisolasjon. Lima/Linux-VM kan tilbys
som et valgfritt sterkere isolasjonslag på maskiner som har nok ressurser, men
er ikke et installasjonskrav. Vanlig Linux får støtte når installasjon,
tjenestehåndtering og rollback er testet der.

## Implementert i v0.3.0-kandidaten

- tekstbasert Telegram-bridge med allowlistet privat chat
- Claude/Codex-routing, abonnementskontroll, fallback og sesjonskontinuitet
- vedvarende innboks og utboks med retry, krasjgjenopptak, aktivitetssignal og
  kommandoene `/new`, `/kill`, `/stop`, `/status`, `/auto`, `/claude` og
  `/codex`; `/stop` avbryter prosessen, tømmer køen og lukker begge
  provider-sesjonene
- idempotent opprettelse av personlig instans og en godkjent, idempotent
  førstesamtale
- innlasting av profil, åpne tråder, minne og påminnelser på hver agenttur
- lokal påminnelsesmotor med separat varsling og forfallsvarsel
- sanitert feilhåndtering og privat lokal audit
- rotert lokal JSONL-audit, filtrert `mundsen-agent logs` og en supportpakke som bare
  inneholder sanitert driftsmetadata
- selvutpakkende installer med administrert Python og isolerte CLI-er
- LaunchAgent og systemd-brukertjeneste
- integritetsverifiserte releases, helsesjekk før og etter oppgradering,
  beskyttet-fil-hashing, backup og automatisk rollback
- ukentlig, metadata-basert GitHub Release-sjekk uten autoinstallasjon;
  manifest og arkivchecksum verifiseres før installasjon
- én felles skillkilde som eksponeres til Claude under `.claude/skills/` og
  Codex under `.agents/skills/`, med kollisjonskontroll
- avskrudd modulgrensesnitt for konfigurasjon, scheduler, kontekst og health
  check
- valgfri `local-health`-modul med privat SQLite, streamingimport av Apple
  Health XML og lokale Markdown-/JSON-sammendrag
- `training-analysis`-skill som analyserer sammendraget, aldri rådatabasen
- sentralt, avskrudd feature-register for private Telegram-vedlegg, lokal
  transkribering, inline SVG/Mermaid, levende progressmelding, utvidede
  kommandoer, autonomikontekst og circuit breaker
- avskrudd connectorregister for kalender, morgenbrief, observability og
  smarthjem via én sanitert lokal kommandokontrakt
- GitHub Actions for macOS/Linux CI og draft-før-publisering av immutable
  releasefiler

Alle nye features og moduler, også `local-health` og connectorplassene, er av
som standard. Connectorene er klare for en lokal backend, men inneholder ingen
private endepunkter, credentials eller leverandørspesifikk konfigurasjon.

## Personvern og sikkerhet

- Persondata og runtime-state skal aldri commits.
- Samtaleinnhold går gjennom Telegram og sendes til den valgte agentleverandøren.
  Ved konfigurert fallback kan samme melding gå til den andre leverandøren hvis
  den første feiler.
- Auditlogger inneholder hendelsesmetadata, ikke samtaletekst som standard.
- Den ukentlige oppdateringssjekken henter bare GitHub release-metadata og det
  lille releasemanifestet. Den sender ingen lokal data tilbake.
- Oppgradering krever eksplisitt godkjenning.
- Ved bruk av en privat fork skal brukeren autentisere med sin egen
  GitHub-identitet. Repoeierens credential skal aldri kopieres til en brukers
  maskin.

Les [PRIVACY.md](PRIVACY.md) og [SECURITY.md](SECURITY.md) før installasjon
eller bidrag.

## Installasjon og oppgradering

Utviklingskandidaten kan hentes fra det offentlige, saniterte repoet:

```sh
git clone https://github.com/powermundsen/MundsenAgent.git "$HOME/MundsenAgent-source"
bash "$HOME/MundsenAgent-source/installer/install.sh" "$HOME/MundsenAgent"
```

Kildeklonen og den private runtimekatalogen må være forskjellige. Installeren
avviser en runtimekatalog i en Git-arbeidskopi og en umerket, ikke-tom katalog.
Dette starter de offisielle installasjonene av manglende CLI-er og går deretter
videre til Telegram-oppsett, abonnementsinnlogging og valgfri lokal
bakgrunnstjeneste. Se:

- [Installasjon](docs/installation.md)
- [Oppgradering og rollback](docs/upgrading.md)
- [Lokal helse og treningsanalyse](docs/local-health.md)
- [Valgfrie runtimefunksjoner](docs/features.md)
- [Valgfrie lokale connectorer](docs/connectors.md)
- [Releaseprosess](docs/releasing.md)

Ikke bruk utviklingskandidaten som produksjonsinstallasjon før den isolerte
ferskinstallasjonen i releaseporten er bestått.

Ikke klon et privat personlig assistentrepo som erstatning for Mundsen Agent. Det kan
eksponere persondata og koble brukerens oppgraderinger til en annen persons
runtime.

## Utvikling

Kode skal bare hentes inn fil for fil etter dokumentert avhengighetsanalyse og
sanitering. Historikk fra et personlig kilderepo skal ikke importeres.

Testene kjører uten leverandørinstallasjon eller eksterne tjenester:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 installer/privacy-scan.py
```

Repoet publiseres med én ren historikk etter full personvernkontroll. Release-
workflowen er med vilje sperret til repository release immutability er aktivert.
Ingen personlig kildehistorikk er en del av det offentlige repoet.

Se [CONTRIBUTING.md](CONTRIBUTING.md) for personvernportene som gjelder for alle
endringer, [avhengighetsanalysen](docs/dependency-analysis.md) for den godkjente
uttrekksgrensen og [CHANGELOG.md](CHANGELOG.md) for endringsformatet.
