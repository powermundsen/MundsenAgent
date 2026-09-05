# Arkitektur

## Formål

Arkitekturen skal gjøre det mulig å dele og oppgradere assistentkode uten å
dele eller overskrive mennesket som bruker den.

## Lag og eierskap

### 1. Mundsen Agent-kjerne

Kjernen er versjonert og read-only under normal drift:

- Telegram-bridge og kø
- agentrouter og fallback
- sesjonskontinuitet og kommandohåndtering
- felles runtime-, path- og konfigurasjonskode
- installer, updater, migreringsport og health check
- standardmoduler og standardskills

Kjernen skal ikke inneholde en ferdig persona eller ekte brukerdata.

### 2. Personlig instans

Instansen opprettes én gang og eies av brukeren:

- `instance.yaml`
- `AGENTS.md` og `CLAUDE.md`
- `MEMORY.md`, `TODO.md` og `reminders.md`
- `memory/user_profile.md` og `memory/open-threads.md`
- lokale modulvalg og private skills

Maler brukes bare ved opprettelse. En senere Mundsen Agent-release kan tilby en diff
eller migrering, men skal ikke regenerere og overskrive filene.

### 3. Runtime og hemmeligheter

Runtime ligger utenfor både release og instans:

- Claude- og Codex-sesjons-ID-er
- Telegram offset, jobbkø og avbruddsstate
- vedlegg og midlertidige visualiseringer
- lokalt auditspor og diagnostikk
- token, deploy key og integrasjonscredential

Runtime kan slettes og bygges opp igjen uten å slette den personlige instansen.
Hemmeligheter skal ikke inngå i backup som standard.

## Avhengighetsretning

```text
Eksterne tjenester
        ^
        | eksplisitt aktivert modul
        |
Bridge -> Router -> Agent-CLI
   |         |          |
   +---------+----------+
             |
       Runtime-grensesnitt
          /         \
 Instansleser     Lokal state
```

Kjernen kjenner bare generiske grensesnitt. En modul kan avhenge av kjernen,
men kjernen skal ikke importere en valgfri modul direkte. Personlig instans er
data, ikke Python-kode som kjernen muterer.

## Katalogmodell i den portable installasjonen

```text
<valgt Mundsen Agent-katalog>/
├── releases/
│   ├── 0.2.0/
│   └── 0.3.0/
├── current -> releases/0.3.0
├── instance/
│   ├── instance.yaml
│   ├── AGENTS.md
│   ├── CLAUDE.md
│   ├── MEMORY.md
│   ├── TODO.md
│   ├── reminders.md
│   ├── memory/
│   └── skills/
├── providers/home/.claude/skills/ -> administrerte skill-lenker
├── providers/home/.agents/skills/ -> administrerte skill-lenker
├── config/
│   ├── runtime.env
│   └── secrets.env
├── state/
│   ├── sessions/
│   ├── queue/
│   ├── attachments/
│   ├── backups/
│   ├── modules/local-health/
│   └── logs/
├── providers/
├── tools/
└── bin/
```

Stiene skal kunne overstyres for tester. Ingen komponent får slå opp en ekte
hjemmekatalog ved importtid.

## Konfigurasjon

`instance.yaml` er den lille, versjonerte brukerrettede kontrakten. Skjema 1
inneholder:

- skjemaversjon og assistentnavn
- språk, tidssone, tone og teknisk nivå
- foretrukket agent

Hemmeligheter refereres med miljøvariabelnavn, aldri som verdier.
Runtimekonfigurasjon valideres før bridge starter.

## Telegram-flyt

1. Long polling henter en oppdatering.
2. Chat-ID kontrolleres før innhold prosesseres.
3. Kommandoer som `/kill`, `/stop`, `/new`, `/status` og modellvalg håndteres.
   `/stop` avbryter aktivt prosesstre, tømmer den vedvarende køen og lukker
   begge provider-sesjonene før neste melding kan routes.
4. Vanlige meldinger lagres på disk før Telegram-offset oppdateres.
5. En avbrytbar FIFO-arbeider gjenopptar usendte jobber etter restart.
6. Router velger Claude eller Codex og injiserer en begrenset allowlist av
   profil, åpne tråder, minne og påminnelser.
7. Responsen lagres før sending, deles i Telegram-kompatible biter og fortsetter
   fra neste usendte tekstbit eller visualiseringsartefakt ved sendefeil.
8. Et gjentatt typing-signal viser aktivitet under lange agentkall.
9. Operasjonell metadata logges lokalt uten meldingstekst.

Private vedlegg, lokal transkribering, inline visualiseringer og redigert
progressmelding ligger bak uavhengige feature-flagg. De inngår ikke i flyten
eller dataområdet før de er eksplisitt aktivert i `MUNDSEN_FEATURES`.

## Oppgraderingsgrense

Updateren har skrivetilgang til releasekatalogen, `current`-pekeren og sin egen
backupmetadata. Den har ikke generell skrivetilgang til personlige filer.

0.3.0 avviser alle releaser som deklarerer migrering. En senere migreringsmotor
må:

- være deklarert i releasemanifestet
- vise hvilke instansfiler og skjema som berøres
- ta backup
- kreve eksplisitt godkjenning
- være idempotent og ha rollbacksteg

## Skills

Kanoniske, delbare skills ligger i releasen. Private skills ligger i
`instance/skills/`. Ved setup og servicestart validerer Mundsen Agent navn og
`SKILL.md`, avviser kollisjoner og lager bare egne administrerte symlinker til
providerens dokumenterte discovery-stier. Urelaterte provider-skills røres
ikke. En releaseoppgradering endrer ikke innhold i private skills.

## Moduler

Telegram og agentrouting er del av minimumskjernen. Valgfrie moduler
registreres gjennom et lite grensesnitt for eksplisitt konfigurasjon,
planlagte lokale jobber, begrenset agentkontekst, varsler og health check.
Moduler er av som standard og aktiveres i `MUNDSEN_MODULES`.

Første referansemodul er `local-health`. Den streamer utvalgte treningsevents
fra en Apple Health XML-eksport til en privat SQLite-database, og genererer et
begrenset Markdown-/JSON-sammendrag. Bare Markdown-sammendraget injiseres i
agentkonteksten. Rå XML og SQLite sendes ikke til agenten.

Kalender, morgenbrief, observability og smarthjem har generiske,
leverandøruavhengige connectorplasser i samme register. De inneholder ingen
backend eller credentials. En aktiv connector kan bare levere begrenset
kontekst, health og eksplisitt aktivert varsling gjennom én lokal executable.
Skriving og kontrollhandlinger er utenfor kontrakten.

En modul skal ikke kunne lese en annen moduls credential eller data med mindre
det er en dokumentert, eksplisitt konfigurert kobling.

## Features

Features endrer bridge-, router- eller kontekstadferd uten å representere en
egen datakilde. Det sentrale registeret deklarerer navn, avhengigheter og
obligatorisk konfigurasjon. Ukjente navn, duplikater og manglende avhengigheter
avvises før bridgen starter. Innstillinger for en avslått feature tolkes ikke.

Nye runtimefunksjoner skal legges til i registeret og bak én avgrenset
implementasjon, med health, test, dataflyt og avskrudd standard. Se
[`features.md`](features.md). Datakilder og planlagte integrasjoner skal bruke
modulregisteret og kontrakten i [`connectors.md`](connectors.md).

## Referanseplattform

Første plattform er native macOS på både eldre Intel Mac og Apple Silicon.
Bridge kjører som LaunchAgent hos en vanlig, upriviligert bruker. En egen lokal
runtimebruker anbefales slik at assistenten ikke automatisk får tilgang til den
vanlige brukerens filer.

Mundsen Agent kjører ikke modellen lokalt. Claude Code og Codex CLI bruker
abonnementsautentisering, så CPU-generasjon og GPU er ikke arkitekturkrav.
Installerens preflight skal i stedet verifisere at de konkrete CLI-versjonene
fungerer på maskinen.

Leverandørinstallasjonene får et tomt prosessmiljø med separate HOME-, XDG- og
credentialkataloger. Dette hindrer at en Mundsen Agent-installasjon finner og endrer
en annen npm- eller CLI-runtime gjennom den innloggede brukerens `PATH`.

Lima/Linux-VM er en valgfri sterkere isolasjonsprofil, ikke
referanseplattformens minimum. Portabilitet vurderes i koden, men støtte
erklæres først når installasjon, tjenestehåndtering og rollback er testet på
plattformen.
