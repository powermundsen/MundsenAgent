# Personvern

## Grunnprinsipp

Mundsen Agent distribuerer kode, ikke mennesker. En personlig instans tilhører
brukeren og skal ikke lastes opp til Mundsen Agent, en sentral tjeneste eller en annen
brukers installasjon.

## Datakategorier

Mundsen Agent skiller mellom:

| Kategori | Eksempler | Lagring |
|---|---|---|
| Plattformkode | bridge, router, installer, standardskills | versjonert Mundsen Agent-release |
| Personlig instans | persona, profil, minne, åpne tråder, påminnelser | lokal instanskatalog, aldri Mundsen Agent-Git |
| Hemmeligheter | bot-token, chat-ID, deploy key, integrasjonstoken | lokal secretsfil eller verktøyets egen credential store |
| Runtime-state | sesjons-ID-er, kø, vedlegg, midlertidige filer | lokal state-katalog |
| Operasjonelle logger | tidspunkt, komponent, resultat, feilkategori | lokal loggkatalog |

Ingen eksempelkonfigurasjon eller testfixture skal inneholde data fra en ekte
person eller installasjon.

## Dataflyt

Når Telegram-modulen er aktiv:

1. Telegram leverer en melding til den lokale bridgen.
2. Bridgen avviser alle chat-ID-er som ikke er eksplisitt tillatt.
3. Routeren sender nødvendig samtalekontekst til Claude Code eller Codex CLI.
4. Svaret sendes tilbake gjennom Telegram.
5. Lokalt minne oppdateres bare når agenten og brukerens instruksjoner krever
   det.

Ved automatisk fallback kan samme melding sendes til begge agentleverandørene
dersom den første feiler. Claude, OpenAI og Telegram behandler data etter
vilkårene for brukerens egne kontoer. Mundsen Agent driver ingen mellomliggende
skytjeneste og mottar ingen kopi.

Valgfrie moduler kan gi flere lokale eller eksterne dataflyter. Hver modul skal
dokumentere datakilder, mottakere, lagring og sletting før den kan aktiveres.

Når `local-health` aktiveres, forblir Apple Health XML og SQLite-databasen
lokale. Mundsen Agent genererer et begrenset treningssammendrag. Dette sammendraget
sendes til valgt agentleverandør når agenten svarer, på samme måte som øvrig
eksplisitt instanskontekst. Rådatabase, eksportfil og import-ID sendes ikke.

Når `attachments` aktiveres, lastes et allowlistet Telegram-vedlegg ned til
privat runtime-state etter chatkontrollen. Originalt filnavn erstattes med et
generert navn. Filen kan sendes til valgt agent som lokal filsti og slettes
etter levert svar eller kansellering. Ved krasj beholdes den bare sammen med
den vedvarende jobben frem til retry eller eksplisitt køtømming.

`local-transcription` sender lyd til en betrodd lokal executable og injiserer
bare transkripsjonen til agenten. `inline-visualizations` lagrer genererte SVG-
eller Mermaid-filer i samme midlertidige stateområde og sletter dem etter
levering. Ingen av funksjonene er aktive som standard.

## Lokal lagring

Den portable installasjonen samler alle Mundsen Agent-data under katalogen brukeren
velger:

- personlig instans: `<Mundsen Agent>/instance`
- konfigurasjon og hemmeligheter: `<Mundsen Agent>/config`
- runtime-state og logger: `<Mundsen Agent>/state`
- cache og nedlastede verktøy: `<Mundsen Agent>/cache` og `<Mundsen Agent>/tools`
- separate CLI-er og credentiallagring: `<Mundsen Agent>/providers`

Operativsystemets tjenesteregistrering ligger i brukerens vanlige LaunchAgents-
eller systemd-katalog og inneholder bare kommando- og runtime-stier, ikke
hemmeligheter eller personlige notater.

Instanskatalogen kan være et eget privat Git-repo dersom brukeren velger det.
Installer og updater skal aldri gjøre dette automatisk.

Hemmelighetsfiler skal ha modus `0600`. Kataloger som inneholder hemmeligheter,
runtime-state eller vedlegg skal ha modus `0700`.

## Logger og audit

Standard audit skal bare inneholde det som trengs for drift:

- tidsstempel
- komponent og hendelsestype
- valgt agent og modell
- varighet, resultat og sanitert feilkategori
- tilfeldige eller lokale korrelasjons-ID-er

Meldingstekst, prompts, svar, vedleggsinnhold, token og personlige profilfelt
skal ikke logges som standard. Debuglogging med samtaleinnhold må være
eksplisitt, tidsbegrenset og forklart til brukeren før aktivering.

## Oppdateringer

Den ukentlige oppdateringssjekken henter GitHub release-metadata og det lille,
maskinlesbare releasemanifestet fra en kontrollert GitHub Release. Mundsen Agent
sender ikke lokal versjon, modulvalg, maskinidentitet eller bruksdata tilbake.

Oppdatering er enveis:

- ny plattformkode kan hentes ned
- personlig konfigurasjon og data lastes ikke opp
- oppgradering installeres ikke uten eksplisitt godkjenning

## Telemetri

Mundsen Agent har ingen sentral telemetri, analyse, crash reporting eller
brukersporing. En valgfri observability-modul må peke på en mottaker brukeren
selv kontrollerer og være av som standard.

## Eksport og sletting

Brukeren kan eksportere den personlige instanskatalogen som vanlige tekstfiler.
Full lokal sletting krever at instans, secrets, state, cache, logger og eventuelle
sikkerhetskopier fjernes. Telegram, Claude og OpenAI kan ha egne
oppbevaringsregler som ligger utenfor Mundsen Agents kontroll.

## Bidrag og testdata

Før commit og release skal repo og historikk skannes for:

- navn, adresser, e-post, telefonnummer og chat-ID-er
- absolutte lokale stier og brukernavn
- hostnavn, private domener, interne IP-er og enhetsnavn
- tokens, passord, OAuth-data og credential-bærende URL-er
- samtaler, minner, vedlegg og logger

Automatisk skann erstatter ikke manuell gjennomgang.
