# Valgfrie runtimefunksjoner

Mundsen Agent skiller mellom **features**, som endrer bridge- eller routeradferd, og
**modules**, som kobler inn en lokal datakilde eller planlagt integrasjon.
Begge deler er av som standard. En feature aktiveres bare når navnet står i den
kommaseparerte `MUNDSEN_FEATURES`-verdien i `config/runtime.env`.

```dotenv
MUNDSEN_FEATURES=attachments,live-progress,extended-commands
```

Ukjente navn, duplikater og manglende avhengigheter stopper oppstart med en
sanitert konfigurasjonsfeil. `mundsen-agent health` viser hvilke features som er
aktive.

## Feature-registeret

`src/mundsen_agent/features.py` er den eneste katalogen over innebygde features. En
ny feature får:

1. én `FeatureSpec` med navn, beskrivelse, avhengigheter og eventuelle
   obligatoriske innstillinger
2. en avgrenset implementasjon i bridge, router eller kontekstlag
3. oppstartvalidering, health check, tester og dokumentasjon av dataflyt

Featurekode skal aldri lese secrets den ikke trenger. Lokale hjelpeprosesser får
et minimalt prosessmiljø uten Telegram-token, GitHub-token eller
agentcredentials. De kjører likevel som den lokale Mundsen Agent-brukeren og må være
betrodde executables.

## `attachments`

Aktiverer private, midlertidige nedlastinger av Telegram-bilder, dokumenter,
lyd og video. Chat-ID kontrolleres før filmetadata eller innhold behandles.
Filen lagres med generert navn, `0600` og en standardgrense på 8 MiB:

```dotenv
MUNDSEN_ATTACHMENT_MAX_BYTES=8388608
```

Originalt filnavn sendes ikke til agenten. Filen ligger under den private
statekatalogen mens jobben kjører, og slettes etter levert svar eller `/kill`.
Ved krasj beholdes den sammen med den vedvarende jobben til retry eller
opprydding. Når filstien gis til Claude eller Codex, kan leverandøragenten lese
innholdet. Aktiver derfor bare feature-en når dette er ønsket.

## `local-transcription`

Krever `attachments` og en eksplisitt lokal executable:

```dotenv
MUNDSEN_FEATURES=attachments,local-transcription
MUNDSEN_TRANSCRIBE_COMMAND=/absolute/path/to/local-transcriber
```

Mundsen Agent kjører executable-en uten shell som:

```text
/absolute/path/to/local-transcriber /private/path/to/input.ogg
```

Transkripsjonen leses fra UTF-8 stdout. Stderr skjules, output er begrenset,
og prosessen arver ikke Mundsen Agent-secrets. Mundsen Agent installerer ingen modell eller
transkriberer automatisk. Dette gjør funksjonen kompatibel med blant annet en
lokal whisper.cpp-wrapper uten å binde kjernen til ett verktøy.

## `inline-visualizations`

Oppdager fenced `svg`- og `mermaid`-blokker i agentsvaret og sender dem som
Telegram-dokumenter. SVG med script, event handlers, `foreignObject` eller
eksterne/datareferanser avvises. Mermaid sendes som `.mmd` med mindre en lokal
renderer er konfigurert:

```dotenv
MUNDSEN_MERMAID_RENDER_COMMAND=/absolute/path/to/renderer-wrapper
```

Wrapperen kalles uten shell med input- og outputsti som to argumenter. Ved feil
sendes den private `.mmd`-kilden i stedet. Midlertidige filer følger samme
retention som innkommende vedlegg. Rendererens SVG-output må bestå samme
kontroll mot script, aktive referanser og eksterne ressurser som direkte SVG.

## `live-progress`

Sender én midlertidig `Thinking…`-melding og redigerer den periodisk under
lange agentkall. Meldingen slettes før svaret leveres. Intervallet er lokalt:

```dotenv
MUNDSEN_PROGRESS_INTERVAL_SECONDS=45
```

Den viser bare forløpt tid, aldri prompt, verktøynavn, filsti eller rå
providerstatus.

## `extended-commands`

Aktiverer følgende Telegram-kommandoer i tillegg til kjernen:

- `/gpt` som alias for `/codex`
- `/version`, `/models` og en ikke-utførende `/rollback <versjon>`-plan
- `/powerup`, sendt som én Claude-tur uten å endre lagret agentmodus
- `/think <nivå>`, `/think-high`, `/gpt-high`, `/codex-high` og `/high`
- installasjonsspesifikke modellaliaser

Modellaliaser er tomme som standard og bruker `alias=model`:

```dotenv
MUNDSEN_CLAUDE_MODEL_ALIASES=quality=provider-model-id
MUNDSEN_CODEX_MODEL_ALIASES=deep=provider-model-id
```

Da blir `/quality` og `/deep` tilgjengelige. Et modellbytte lukker den aktuelle
provider-sesjonen før neste tur. Reserverte bridgekommandoer kan ikke brukes som
alias. `/rollback` gjør aldri endringen fra Telegram, men viser den lokale
maintenance-kommandoen som må godkjennes og kjøres separat.

## `autonomy-context`

Injiserer den lokale `instance/AUTONOMY.md` på hver providertur. Setup lager en
konservativ mal. Filen må finnes, men har ingen effekt før feature-en
aktiveres. Harde sikkerhetsregler i `AGENTS.md` og `CLAUDE.md` har alltid
prioritet.

## `circuit-breaker`

I auto-modus kan en provider som feiler gjentatte ganger hoppes over i en kort
periode. Tvunget `/claude` eller `/codex` ignorerer kretsen. Standardverdiene
brukes bare når feature-en er aktiv:

```dotenv
MUNDSEN_CIRCUIT_BREAKER_THRESHOLD=3
MUNDSEN_CIRCUIT_BREAKER_COOLDOWN_SECONDS=300
```

Tilstanden er med hensikt bare i minnet. Restart åpner begge providere igjen.
