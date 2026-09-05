# Avhengighetsanalyse og uttrekksforslag

## Formål

Dette dokumentet kartlegger de vurderte kildefilene fra en eksisterende privat
assistent uten å importere kildehistorikk eller persondata. Analysen bestemmer
hva som kan gjenbrukes, hva som må skrives om og hva som skal utelates.

Ingen hel kildefil eller kildehistorikk er kopiert til Mundsen Agent. Den godkjente
elleve-filers grunnpakken er nå implementert som ny, sanitert kode etter
kontraktene i dette dokumentet.

## Metode og status

Kartleggingen kombinerte:

- Python AST for imports, toppnivådefinisjoner og miljøvariabler
- tekstsøk etter absolutte stier, identiteter, integrasjoner og credential
- gjennomgang av prosesskall, statefiler, logger og nettverksflyt
- baselinekjøring av kildens isolerte testsuite

Baseline var 126 beståtte tester. Testene verifiserer nyttig adferd, men flere
fixtures og testgrupper er personlige eller modulkoblede og skal derfor ikke
kopieres direkte.

## Nåværende avhengighetsgraf i kilden

```text
telegram-claude-bridge.py
├── runtime_env.py
├── agent_router.py
│   └── splunk_audit.py
├── splunk_audit.py
└── telegram_bridge_utils.py

send-telegram-message.py
└── runtime_env.py

test_bridge.py
├── agent_router.py
├── telegram_bridge_utils.py
├── telegram-claude-bridge.py, dynamisk import
└── en privat webhook-bridge, dynamisk import
```

Alle runtimefilene bruker bare Python-standardbiblioteket. Eksterne
runtimeavhengigheter er Telegram Bot API, Claude Code CLI, Codex CLI og Git.
Den gamle VM-prototypen bruker i tillegg Lima, Ubuntu og systemd, men dette er
ikke nødvendig for native drift på en eldre Intel Mac.

## Filvis vurdering

### `telegram-claude-bridge.py`

**Størrelse og rolle:** Omtrent 1500 linjer. Long polling, chatkontroll,
vedlegg, formattering, kø, avbrudd, progressmeldinger, callbacks, varsling,
nettverksrecovery og prosesslivssyklus ligger i samme fil.

**Direkte lokale avhengigheter:**

- `runtime_env.py`
- `agent_router.py`
- `splunk_audit.py`
- `telegram_bridge_utils.py`

**Tillatt logikk etter omskriving:**

- avvisning av ukjent chat før kø og agentkall
- FIFO-kø med én aktiv jobb
- cancellation token for `/kill` og nødstoppen `/stop`
- `/new`, `/stop`, `/status`, agentvalg og modellvalg
- Telegram-klient for JSON-kall, HTML-svar og dokumentvedlegg
- reply-kontekst og callbackvalg
- sanitert feilrespons
- kontrollert signalhåndtering

**Må fjernes eller skilles ut:**

- importtidslesing av hemmeligheter og oppretting av ekte runtimekataloger
- direkte lesing av en agentleverandørs settingsfil
- hardkodet persona, chat-ID, modell og personlig språk
- logging av meldingstekst eller tekstpreview
- direkte Splunk-kobling
- macOS-spesifikk DNS-flush og Wi-Fi-styring
- YouTube-nedlasting
- modellspesifikk kontekstprising og kostnadstekst
- globale statefiler under en bestemt agentkatalog

**Konklusjon:** Hybrid. Filen skal ikke kopieres. Gjenbruk logikken på
funksjonsnivå og del den i Telegram-klient, bridge-runtime og kommandoadapter.

### `agent_router.py`

**Størrelse og rolle:** Omtrent 2500 linjer. CLI-prosesskontroll, parsing,
routing, fallback, circuit breakers, modeller, kontinuitet, kontekstbygging,
Git-versjonering og spesialkommandoer er blandet sammen.

**Direkte lokal avhengighet:** `splunk_audit.py`.

**Tillatt logikk etter omskriving:**

- felles `AgentResponse` og `RouterState`
- kontrollert subprocess med timeout og cancellation
- parsing av Claude stream-JSON og Codex NDJSON
- session resume og kontroll av resume-mismatch
- fallbackklassifisering og circuit breakers
- lokal kontinuitet for korte oppfølginger
- `/claude`, `/codex`, `/auto`, `/status`, `/new` og `/stop`
- sanitert brukerfeil

**Må gjøres konfigurerbart:**

- binærstier, arbeidskatalog, timeouts og statekatalog
- modellkatalog, alias, standardmodell og fallbackrekkefølge
- språk og tekstmaler
- agentenes CLI-argumenter og støttede reasoningnivåer
- hvilke lokale kontekstleverandører som er aktive

**Må fjernes eller flyttes:**

- personlig navn og personspesifikk svarguidance
- kalenderlesing fra vertsoperativsystemet
- private repo-stier og instruksjoner for live sync
- Git-rollback av kildekode fra chat
- YouTube-kommando og hardkodet chat-ID
- modellpristabeller og valutakonvertering
- direkte Splunk-audit
- emnespesifikk klassifisering for helse, smarthjem og private dashboards

**Konklusjon:** Hybrid. Gjenbruk algoritmer og testkontrakter, men skriv en ny
router delt i CLI-adapter, modeller, kontinuitet, kommandoer og orkestrering.

### `telegram_bridge_utils.py`

**Størrelse og rolle:** Omtrent 230 linjer. Markdown- og HTML-konvertering,
tabellrendering, chunking og uttrekk av SVG/Mermaid.

**Tillatt direkte etter liten sanitering:**

- escaping og allowlist av Telegram-HTML
- markdown, kodeblokker og tabeller
- chunking etter ferdig HTML-lengde

**Flyttes ut av minimumet:**

- skriving av visualiseringsfiler. Dette krever egen vedleggs- og
  retention-policy.

**Konklusjon:** Kjerne. Dette er den reneste første uttrekkskandidaten.

### `runtime_env.py`

**Størrelse og rolle:** Omtrent 40 linjer. Leser miljøvariabler og faller
tilbake til en leverandørspesifikk settingsfil i hjemmekatalogen.

**Tillatt idé:**

- ett sentralt konfigurasjonslag
- miljøvariabler kan overstyre filkonfigurasjon

**Må skrives om:**

- Mundsen Agent skal lese egne config- og secretsfiler
- stier skal injiseres og kunne peke til et midlertidig testmiljø
- ingen agentleverandørs settingsfil skal brukes som generell secretskilde
- validering skal returnere strukturerte feil, ikke mutere miljøet ved import

**Konklusjon:** Kjerneidé, ny implementasjon.

### `splunk_audit.py`

**Størrelse og rolle:** Omtrent 50 linjer. Skriver JSONL til en hardkodet
primærsti og en global midlertidig fallbacksti.

**Tillatt idé:**

- best-effort lokal JSONL-audit
- defensiv serialisering og feltgrenser

**Må skrives om:**

- generisk navn og injisert statekatalog
- ingen hostname, chat-ID, session-ID, meldingstekst eller rå feil som standard
- eksplisitt allowlist for auditfelt
- private filrettigheter og sikker rotasjon
- Splunk-forwarding blir en senere, valgfri modul

**Konklusjon:** Lokal audit er kjerne. Splunk er ikke kjerne.

### `send-telegram-message.py`

**Størrelse og rolle:** Omtrent 80 linjer. En separat CLI for å sende én
Telegram-melding.

Logikken overlapper Telegram-klienten og er ikke nødvendig for første
runtimecommit. Senere kan en tynn administrasjonskommando bygges oppå samme
klient, med eksplisitt bekreftelse i kallende arbeidsflyt.

**Konklusjon:** Utsettes.

### `test_bridge.py`

**Størrelse og rolle:** Omtrent 1700 linjer og 126 baselinetester.

**Testkontrakter som kan gjenskapes med falske data:**

- fallbackmønstre og circuit breakers
- agentkommandoer og modellvalg
- kontinuitet, pending interaction, `/new` og `/stop`
- secretsanitering
- Claude- og Codex-outputparsing
- CLI-argumenter og session resume
- Telegram-formattering, reply-kontekst, kø og cancellation
- tomt agentsvar og brukerrettet feilhåndtering

**Skal ikke kopieres:**

- ekte navn, chat-ID-er, stier eller leverandørtoken
- tester for private webhooker eller domeneintegrasjoner
- modellpriser og lokal valutaberegning
- personspesifikk kvalitetsprompt, kalender eller rollbacktekst
- tester som krever eller kan finne ekte hjemmekatalog

**Konklusjon:** Bruk som adferdsreferanse. Skriv nye tester med isolert
`HOME`, XDG config, state og cache.

### Privat bridgeprotokoll og gammel VM-prototype

Designprinsippene for dual-agent fallback, kontinuitet, kø, `/kill` og `/stop` er
generiske og kan omskrives. Den gamle VM-prototypen gir nyttig erfaring med
Lima, interaktiv CLI-auth, systemd og idempotent filopprettelse.

Følgende mønstre skal ikke videreføres:

- kloning av et personlig repo
- credential i Git remote-URL
- automatisk installasjon fra `main`
- inplace-kopiering av nye runtimefiler
- automatisk restart uten godkjent release
- private observability-endepunkter og personlige tjenestenavn

## Målarkitektur for kode

```text
src/mundsen/
├── config.py
├── paths.py
├── audit.py
├── bridge/
│   ├── telegram_client.py
│   ├── telegram_format.py
│   └── runtime.py
├── router/
│   ├── models.py
│   ├── cli.py
│   ├── continuity.py
│   ├── commands.py
│   └── router.py
└── updater/
    ├── manifest.py
    └── releases.py
```

Avhengighetsretningen skal være:

```text
paths <- config <- bridge/runtime
  ^         ^          |
  |         |          v
audit <-----+------ router

telegram_format <- telegram_client <- bridge/runtime
```

Ingen modul får utføre filskriving, nettverkskall eller prosessstart ved import.

## Første kodecommit, gjennomført

Commit `63e5747` etablerer bare disse trygge bladmodulene:

1. `pyproject.toml`
2. `src/mundsen/__init__.py`
3. `src/mundsen/paths.py`
4. `src/mundsen/config.py`
5. `src/mundsen/audit.py`
6. `src/mundsen/bridge/__init__.py`
7. `src/mundsen/bridge/telegram_format.py`
8. `tests/test_paths.py`
9. `tests/test_config.py`
10. `tests/test_audit.py`
11. `tests/test_telegram_format.py`

Pakken kopierer ikke en hel kildefil. Formatteringsalgoritmene er saniterte
fra hjelpefilen. Config og audit er implementert på nytt etter kontraktene i
`PRIVACY.md` og `SECURITY.md`. Alle 36 nye tester består.

Commiten skal ikke inneholde:

- Telegram-nettverk
- agentprosess eller modellnavn
- personlig instansmal
- installer eller service
- updater
- valgfri modul

Det gir en liten, testbar grunnmur før router og bridge trekkes ut.

## Plan etter første kodecommit

1. Legg til routerens datamodeller, CLI-parsere og subprocess-kontroll.
2. Legg til session og kontinuitet med XDG-state og atomisk filskriving.
3. Legg til routing, fallback og generisk kommandokatalog.
4. Legg til Telegram-klient og kø-runtime.
5. Definer `instance.yaml`, generer tom personlig instans og koble instruksjoner
   inn som data.
6. Bygg native macOS-installer og LaunchAgent. Legg Lima og systemd til som en
   separat, valgfri isolasjonsprofil senere.
7. Bygg release-manifest, metadata-sjekk, godkjenning, atomisk oppgradering og
   rollback.

Hvert trinn får egne tester og personvernport før neste trinn.

## Godkjenningsport

Prosjekteieren godkjente 2026-07-28 både den avgrensede elleve-filers
kodecommitten og kontrollert omskriving av den korte Mundsen Agent-historikken.
Begge handlingene er gjennomført og pushet.

Godkjenningen omfattet bare de elleve kode- og testfilene over. Router,
Telegram-nettverk, installer, runtime/deploy og updater krever nye,
avgrensede gjennomganger når de konkrete diffene er klare.
