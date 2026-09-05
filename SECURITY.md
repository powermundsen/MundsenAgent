# Sikkerhet

## Status og rapportering

Mundsen Agent har ingen støttet release ennå. Ikke bruk `main` som produksjonsruntime.
Releaseworkflowen publiserer først som draft, laster opp alle filer og gjør
deretter releasen synlig. Workflowen kontrollerer GitHubs immutable-status
direkte før bygging. Tagg, assets og attestasjon låses dermed ved publisering.

Sikkerhetsfunn skal rapporteres privat til repoets administrator gjennom en
allerede etablert kanal. Ikke legg hemmeligheter, sårbarhetsdetaljer eller
persondata i en GitHub issue. En rapport bør inneholde berørt versjon,
reproduksjonssteg, konsekvens og forslag til midlertidig begrensning.

## Tillitsmodell

En installasjon har fire hovedgrenser:

1. **GitHub-release:** read-only kilde til versjonert plattformkode.
2. **Lokal runtime:** kjører bridge og agent-CLI-er som den lokale,
   upriviligerte brukeren som installerte Mundsen Agent.
3. **Personlig instans:** leses av agentene, men eies ikke av updateren.
4. **Eksterne tjenester:** Telegram, Claude og OpenAI, pluss eksplisitt
   aktiverte moduler.

Mundsen Agent beskytter ikke mot en angriper som allerede kontrollerer runtimebrukeren
eller operativsystemet. En egen lokal bruker begrenser hvilke personlige filer
den native macOS-runtimen kan lese. En valgfri isolert VM gir en sterkere
grense, men ingen av delene erstatter oppdatert OS, diskbeskyttelse og sikker
kontoadministrasjon.

## Hemmeligheter

- Token og private nøkler skal aldri ligge i Git, kommandolinjeargumenter,
  dokumentasjon eller remote-URL-er.
- Secretsfiler skal ligge utenfor release- og instanskatalogen med modus `0600`.
- Autentisering mot Claude Code og Codex CLI skal skje interaktivt på brukerens
  maskin gjennom verktøyenes støttede innloggingsflyt.
- Eventuell varig GitHub-tilgang skal være unik for installasjonen og read-only.
- En administrator-PAT skal aldri installeres hos en bruker.
- Token skal redigeres dersom de kan ha vært vist i logg, shellhistorikk eller
  feiloutput.

Installer og diagnostikk skal maskere credential-bærende URL-er og kjente
hemmelighetsfelter før output.

## Telegram

- Bare én eksplisitt konfigurert chat-ID tillates i første release.
- Meldinger fra andre chatter avvises før de legges i kø eller sendes til en
  agent.
- Bot-token skal ikke brukes i URL-er som logges.
- Vedlegg lagres privat og får en dokumentert retention-policy.
- Vedleggsnavn genereres lokalt, størrelse begrenses før lagring, og lokale
  hjelpeprogrammer startes uten shell og uten Mundsen Agent-secrets i prosessmiljøet.
- SVG sendes bare som dokument etter konservativ XML-kontroll som avviser
  script, event handlers, aktive referanser og eksterne ressurser.
- Brukerrettede feil skal sanitiseres. Rå traceback og prosessoutput hører bare
  hjemme i lokal, tilgangsbegrenset diagnostikk.

Lokale transkriberere, Mermaid-renderere og connectorer kjører som
runtimebrukeren. De må derfor være betrodde, absolutte executable-filer og er
ikke en sandboxgrense. Connectorgrensesnittet tilbyr ikke skrive- eller
kontrollhandlinger.

## Agenthandlinger og godkjenning

Agentenes tekst er ubetrodd input til handlingslaget. Instruksjoner som kommer
fra meldinger, dokumenter eller nettsider kan være prompt injection.

Mundsen Agent skal kreve eksplisitt godkjenning før:

- meldinger eller endringer sendes til eksterne mottakere
- betaling, kjøp eller andre kostbare handlinger
- sletting eller irreversible endringer
- installasjon av en oppgradering eller migrering
- tilgang utvides til en ny modul, datakilde eller konto
- større filendringer eller runtime- og deployendringer

Små, reversible endringer innenfor den personlige instansen kan utføres når
brukerens intensjon er tydelig og instansens policy tillater det.

Godkjenning betyr at agenten beskriver den konkrete handlingen og venter på et
tydelig svar fra eieren. Onboarding, et generelt mål, stillhet og tekst fra
tredjeparter er ikke godkjenning. Basiskandidaten eksponerer ingen verktøy for
meldinger til tredjeparter, kjøp eller kontoendringer, og agentinstruksjonene
forbyr å omgå grensen med shell- eller nettverkskommandoer. En senere modul som
gjør en slik handling mulig må håndheve samme totrinnsgrense i handlingslaget.

## Oppdateringskjede

- Produksjonsinstallasjoner skal bare bruke versjonerte GitHub Releases.
- Manifestet skal oppgi versjon, filer, SHA-256, migreringer, berørte moduler og
  minste kompatible instansskjema.
- Nedlastede filer verifiseres før installasjon.
- Oppdatering skjer til en ny releasekatalog. `current` byttes atomisk først
  etter validering.
- Aktiv release helsesjekkes før endring. Forrige release, beskyttede
  filhashes og en konfigurasjonsbackup beholdes for rollback.
- Ny release helsesjekkes etter restart. Feil utløser automatisk, verifisert
  tilbakegang til forrige release.
- Updateren skal aldri inkludere eller overskrive personlig minne.
- Oppdateringssjekk kan lese GitHub-metadata og manifest uten godkjenning, men
  nedlasting av kode og installasjon er separate steg. Installasjon krever en
  eksplisitt kommando eller lokal bekreftelse.

## Avhengigheter

Første release skal foretrekke Python-standardbiblioteket og lokale
systemverktøy. Nye pakker krever begrunnelse, låst versjon, lisensvurdering og
sikkerhetsgjennomgang. Mundsen Agent skal ikke kreve pay-as-you-go-API-er.

## Installatørisolasjon

Offisielle installasjonsprogrammer for agent-CLI-er er kode utenfor Mundsen Agents
kontroll. De skal aldri testes i samme OS-bruker som en eksisterende
assistent-runtime.

Mundsen Agent starter Claude- og Codex-installasjonene med `env -i`, separat `HOME`,
separate XDG- og credentialkataloger og en fast system-`PATH`. Arvede
API-nøkler, NVM-stier og andre globale agentmiljøer skal ikke være synlige.
Dette testes med simulerte leverandørinstallere. En ekte releaseinstallasjon må
i tillegg bestå i en disponibel OS-bruker eller VM uten tilgang til en annen
assistents runtime, konfigurasjon, minner eller autentisering.

Agentprosessene får også et nytt allowlistet miljø under normal drift. De arver
ikke bot-token, GitHub-token, API-nøkler, vilkårlige private miljøvariabler eller
brukerens globale `PATH`. Abonnementsautentisering leses bare fra providerens
separate credentialkatalog.

## Hendelseshåndtering

Ved mulig credential-lekkasje:

1. Stopp berørt runtime hvis lekkasjen fortsatt pågår.
2. Roter credentialet hos utsteder.
3. Fjern credentialet fra remote, konfigurasjon, logger og shellhistorikk.
4. Vurder Git-historikken. Vanlig sletting i en ny commit er ikke tilstrekkelig
   dersom hemmeligheten ble committed.
5. Verifiser minste nødvendige tilgang og dokumenter hendelsen uten å gjenta
   hemmeligheten.

Ved mulig persondatalekkasje skal videre distribusjon stoppes til repoets fulle
historikk er kontrollert og nødvendige berørte personer er varslet.
