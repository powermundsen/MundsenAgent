# Endringslogg

Alle vesentlige endringer dokumenteres her. Formatet følger prinsippene i
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), og versjoner følger
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Hver release skal skille mellom funksjoner, sikkerhet, personvern, migrering og
kompatibilitetsbrudd. `release-manifest.json` er den maskinlesbare kontrakten.

## [Unreleased]

### Added

- Avskrudd feature-register med deklarerte avhengigheter og påkrevde
  innstillinger. Ukjente eller ufullstendige featurevalg stopper ved oppstart.
- Valgfrie private Telegram-vedlegg med størrelsesgrense, genererte filnavn,
  krasjtrygg levetid og opprydding etter svar eller kansellering.
- Valgfri lokal tale-/lydtranskribering gjennom én eksplisitt executable som
  kjøres uten shell og uten Mundsen Agent-secrets i miljøet.
- Valgfri uttrekking av sikre SVG- og Mermaid-blokker til Telegram-dokumenter,
  med valgfri lokal Mermaid-renderer og retryposisjon per artefakt.
- Valgfri levende Telegram-progress, utvidede modell-/reasoningkommandoer,
  ikke-utførende `/rollback`-plan, autonomikontekst og in-memory circuit breaker.
- Registerbaserte, avskrudd lokale connectorer for kalender, morgenbrief,
  observability og smarthjem. Den felles kontrakten gir bare kontekst, health
  og eksplisitt aktivert varsling, ikke kontrollhandlinger.
- Telegram-kommandoen `/stop` avbryter aktiv providerprosess, tømmer både
  minne- og diskkøen og lukker Claude- og Codex-sesjonene uten resume.
- Egen kapabilitetsgrense (`router/capabilities.py`) for provideragentene:
  Claude får bare lokale filverktøy (`Read`, `Edit`, `Write`, `Glob`, `Grep`)
  og Codex kjører bare i `read-only`/`workspace-write`-sandbox. Adapterne
  validerer grensen ved konstruksjon, så en konfigurasjon med et verktøy eller
  en sandbox som kan nå utenfor instansen feiler ved oppstart, ikke ved første
  agentkall. Testet i `tests/test_capabilities.py`.
- GitHub Actions-CI på macOS/Linux og Python 3.10/3.12/3.14, issue-/PR-maler,
  `CODEOWNERS`, MIT-lisens og en releaseworkflow som bygger draft, laster opp
  alle assets og først deretter publiserer.
- Offentlig distribusjon fra én ny, sanitert rotcommit. Tidligere privat
  utviklingshistorikk beholdes separat og eksponeres ikke.
- Releaseartefakter med maskinlesbart manifest, samlet `SHA256SUMS` og en
  sperre som krever eksplisitt bekreftet GitHub release immutability.
- `mundsen-agent update check|download|install` for stabil GitHub Release-discovery,
  manifestvisning, checksumverifisert nedlasting og kontrollert installasjon
  gjennom eksisterende health-, backup- og rollbackflyt.
- Ukentlig updatevarsel med berørte moduler, persondataeffekt og migreringer.
  Sjekken installerer aldri automatisk.
- Felles skill-discovery for Claude `.claude/skills/` og Codex
  `.agents/skills/`, med én kanonisk kilde, administrerte symlinker,
  kollisjonskontroll og health check.
- Avskrudd modulgrensesnitt for konfigurasjon, planlagte lokale jobber,
  agentkontekst, varsler og health check.
- Valgfri `local-health`-modul med privat SQLite, streamingimport av Apple
  Health XML, eventdeduplisering og lokale Markdown-/JSON-sammendrag.
- Bundled `training-analysis`-skill med tydelig grense mellom
  treningsrefleksjon og medisinsk rådgivning.
- `mundsen-agent logs` for filtrert lokal driftsmetadata og `mundsen-agent support-bundle`
  for en privat, sanitert diagnostikkpakke uten config eller helsedata.
- Arbeidskopi- og historikkskann for private stier, credentials, private nett,
  runtimefiler og reserverte private identifikatorer.

### Changed

- Prosjektet er omdøpt fra utviklingsnavnet til Mundsen Agent før første støttede
  release. Python-pakke, CLI, `MUNDSEN_*`-innstillinger, runtime-stier,
  service-ID-er, releaseartefakter og standard update-repo bruker samme navn.
- CI bruker pinned checkout v7.0.1, setup-python v7.0.0 og upload-artifact
  v7.0.1, og tester nå også Python 3.14.
- Modulopprettelse bruker nå et sentralt factoryregister i stedet for en
  hardkodet navnesjekk, slik at nye integrasjoner får samme aktiverings- og
  health-grense.
- Setup oppretter en inaktiv `AUTONOMY.md`-mal og alle valgfrie
  konfigurasjonsfelt med tom eller avskrudd standard.
- Lokal JSONL-audit roteres ved 5 MiB og beholder fem private segmenter.
- Supportpakker krever en ny absolutt fil i en eksisterende katalog og endrer
  aldri rettighetene på katalogen brukeren valgte.
- GitHub asset-redirects er allowlistet, og Authorization-header fjernes før
  nedlasting fra GitHubs separate assetdomene.

### Fixed

- `install.sh` sammenligner nå en gjenoppbygd payload mot en allerede
  installert utgivelse med samme versjonsnummer via innholdshash
  (`release.payload_matches_release`). En omkjøring med endret kode under
  samme versjon feiler tydelig i stedet for å beholde den gamle utgivelsen
  stille. En uendret payload installeres fortsatt på nytt uten feil.
- `mundsen-agent training` uten underkommando velger nå `status` som dokumentert.
- Nedlastede releasearkiv checksumverifiseres før kommandoen omtaler dem som
  verifisert.

### Privacy

- Innkommende vedlegg og genererte visualiseringer lagres bare i privat
  runtime-state og slettes etter fullført levering eller kansellering. Lokale
  hjelpeprosesser arver ikke bot-token, GitHub-token eller agentcredentials.
- Connectorer er lokale executables og får ingen secrets gjennom prosessmiljøet.
  Connectorens begrensede kontekst sendes bare til agent når modulen er aktiv.
- Helseeksport, SQLite, sammendrag og modulstate ligger utenfor Git og
  releasebackup. Bare det begrensede Markdown-sammendraget kan injiseres i
  agentkonteksten etter eksplisitt modulaktivering.
- Supportpakker utelater secrets, config, samtaler og rå helse-/treningsdata.

### Migration

- Ingen automatiske migreringer. Eksisterende runtime får ikke nye features
  eller moduler før eieren legger dem til i `runtime.env`. Nye instanser får en
  inaktiv `AUTONOMY.md`-mal.
- Det finnes ingen støttet installasjon som må migreres fra utviklingsnavnet.
  Gamle pakke-, CLI- og miljøvariabelnavn beholdes derfor ikke som aliaser.

## [0.2.0] - 2026-07-28

### Added

- Grunnleggende dokumentasjon for formål, arkitektur, personvern, sikkerhet,
  installasjon og kontrollert oppgradering.
- Første referanseplattform definert som native macOS på både eldre Intel Mac
  og Apple Silicon. Lima/Linux-VM er valgfri isolasjon, ikke et krav.
- Filvis avhengighetsanalyse og foreslått allowlist for første saniterte
  kodeuttrekk.
- Python-packagegrunnlag med injiserbare XDG-stier, streng lesing av runtime-
  og secretskonfigurasjon, privat lokal JSONL-audit og sikker
  Telegram-formattering.
- Telegram-klient, vedvarende innboks og utboks, krasjgjenopptak, retry per
  svarbit, aktivitetssignal, Claude/Codex-router, sesjonskontinuitet og
  saniterte brukerfeil.
- Interaktivt instans- og Telegram-oppsett, abonnementskontroll, health check,
  LaunchAgent, systemd-brukertjeneste og kommandolinjeverktøy.
- Selvutpakkende installer med administrert Python, offisielle agent-CLI-er,
  releasebygging, verifisert lokal oppgradering og atomisk rollback.
- Godkjent og idempotent førstesamtale, varig kontekstinnlasting og lokal
  påminnelsesmotor.
- 147 isolerte tester for stier, config, audit, Telegram, router, prosesskontroll,
  installer, tjenester, releases og simulert ende-til-ende-flyt.

### Security

- Kontrakt for lokale hemmeligheter, read-only deploy key, eksplisitt
  godkjenning og sanitert logging.
- Private katalog- og filrettigheter, avvisning av symlinker og hemmeligheter
  samt allowlistede auditfelt er håndhevet i grunnpakken.
- Leverandørinstallasjoner kjører i tomt, allowlistet miljø med separate HOME-,
  XDG- og credentialkataloger. Simulert test verifiserer at global npm-,
  NVM- og API-konfigurasjon ikke arves.
- Agentprosesser får et separat allowlistet runtime-miljø uten bot-token,
  GitHub-token, API-nøkler, vilkårlige private variabler eller global `PATH`.
- Releasemanifest og releasearkiv avviser symlinker, traversering, ugyldige
  typer, for store filer og checksumavvik.
- Installeren skiller kilde fra runtime, avviser Git-arbeidskopier og umerkede
  ikke-tomme kataloger, og godtar aldri endret innhold i en eksisterende
  versjon.
- Oppgradering krever grønn health check før endring, tar backup og
  beskyttet-fil-hash, verifiserer den nye releasen etter restart og gjenoppretter
  forrige verifiserte release automatisk ved feil.
- Eksterne handlinger krever en konkret, separat eierbekreftelse.

### Privacy

- Strengt skille mellom versjonert plattformkode, personlig instans og lokal
  runtime-state.
- Onboardingen forklarer korrekt at meldinger går gjennom Telegram og valgt
  modellleverandør, og at en native agentprosess kan lese det OS-brukeren kan
  lese selv om Mundsen Agent bare bruker filer utenfor instansen etter bestilling.
- Pakken utfører ingen filskriving, nettverkskall eller prosessstart ved import.
  Testene bruker midlertidige kataloger, falske leverandører og ingen ekte
  hjemmekatalog eller abonnementskall.

### Migration

- Ingen migreringer. 0.2.0 avviser manifest som deklarerer migrering.

### Breaking

- Runtime og kildekode kan ikke lenger dele Git-arbeidskopi. Bruk en separat,
  tom runtimekatalog.
- Den interne 0.1.0-kandidaten erstattes av 0.2.0. Det finnes ingen støttet
  produksjonsinstans som må migreres.
