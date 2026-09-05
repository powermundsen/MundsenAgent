# Valgfrie lokale connectorer

Kalender, morgenbrief, observability og smarthjem varierer mellom
installasjoner. Mundsen Agent inneholder derfor ingen private endepunkter, kontonavn,
enhetsnavn eller credentials. I stedet ligger fire funksjonelle, men avskrudd
connectorplasser klare i modulregisteret:

| Modul | Lokal executable |
|---|---|
| `calendar` | `MUNDSEN_CALENDAR_COMMAND` |
| `morning-brief` | `MUNDSEN_MORNING_BRIEF_COMMAND` |
| `observability` | `MUNDSEN_OBSERVABILITY_COMMAND` |
| `smart-home` | `MUNDSEN_SMART_HOME_COMMAND` |

Aktivering er eksplisitt:

```dotenv
MUNDSEN_MODULES=calendar,observability
MUNDSEN_CALENDAR_COMMAND=/absolute/path/to/calendar-connector
MUNDSEN_OBSERVABILITY_COMMAND=/absolute/path/to/observability-connector
```

Executable-en må være en absolutt, ikke-symlinket fil. Den startes uten shell
og med et minimalt miljø som ikke inneholder Telegram-token, GitHub-token eller
providercredentials. Den kjører fortsatt som den lokale Mundsen Agent-brukeren og må
derfor være en betrodd lokal executable.

## Kommandokontrakt

Samme executable støtter fire operasjoner:

```text
connector context
connector health
connector notifications 2026-08-18T06:30:00+00:00
connector ack module:key
```

- `context` skriver maksimalt 64 KiB UTF-8-kontekst til stdout. Innholdet
  injiseres til valgt agent og må derfor være et bevisst, begrenset sammendrag.
- `health` returnerer exitkode 0 når lokal backend er klar.
- `notifications` skriver en JSON-liste med `{ "key": "module:id", "text":
  "..." }`. Maksimalt 20 varsler godtas per sjekk.
- `ack` persisterer at ett varsel er levert. Connectoren skal deretter ikke
  returnere det samme varselet på nytt.

Varsling er av selv når modulen er aktiv. Det må slås på per connector:

```dotenv
MUNDSEN_CALENDAR_NOTIFICATIONS=1
MUNDSEN_MORNING_BRIEF_NOTIFICATIONS=1
MUNDSEN_OBSERVABILITY_NOTIFICATIONS=1
MUNDSEN_SMART_HOME_NOTIFICATIONS=1
```

Connectorgrensen tilbyr bare kontekst, health og varsling. Den tilbyr ikke
kalenderskriving, meldinger, smarthjemstyring eller andre handlinger. En senere
handlingsmodul må ha en separat totrinns godkjenningskontrakt.

## Legge til en ny connector

En ny innebygd connector krever én registrering i
`builtin_module_registry()`. Bruk `connector_factory()` når kontrakten over er
tilstrekkelig. Egen Python-implementasjon er bare nødvendig når modulen trenger
annen lokal state eller databehandling. Alle moduler må dokumentere datakilde,
agentkontekst, lokal lagring, varsling, sletting og eventuelle eksterne
mottakere før de kan aktiveres.
