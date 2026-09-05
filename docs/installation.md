# Installasjon

## Status

Installer, oppsett, agentautentisering og lokal tjeneste er implementert i
utviklingskandidaten. `main` skal fortsatt ikke brukes som produksjonsruntime
før en fersk installasjon er verifisert i en separat OS-bruker eller VM uten
tilgang til en eksisterende assistent.

## Første referanseplattform

- macOS på Intel eller Apple Silicon
- `curl`, `tar` og minst 1,5 GiB ledig diskplass
- tilgang til det offentlige Mundsen Agent-repoet eller en versjonert installasjonspakke
- Claude- og ChatGPT-abonnement for agentene som skal brukes
- egen Telegram-bot og én godkjent privat chat

Installeren henter administrert Python 3.12 og de offisielle Claude Code- og
Codex CLI-installasjonene under den valgte Mundsen Agent-katalogen. Maskinen trenger
ikke lokal modell, GPU eller Apple Silicon.

Native macOS er standard. En egen lokal runtimebruker anbefales for å begrense
filtilgang. Lima med Linux-VM blir et valgfritt isolasjonsvalg for maskiner som
har nok ressurser. Vanlig Linux regnes ikke som støttet før installasjon,
tjenestehåndtering og rollback er testet der.

## Tilgang som må opprettes

Brukeren eller administratoren må ha:

1. En egen Telegram-bot hos BotFather.
2. Tilgang til det offentlige Mundsen Agent-repoet eller en kontrollert
   installasjonspakke. Vanlig lesing krever ikke GitHub-innlogging.
3. Claude-abonnement dersom Claude skal brukes.
4. ChatGPT-abonnement dersom Codex skal brukes.

En administrator-PAT skal ikke installeres hos brukeren. Private nøkler og
token skal aldri sendes i chat eller lagres i repoet.

## To kommandoer fra repo

```sh
git clone https://github.com/powermundsen/MundsenAgent.git "$HOME/MundsenAgent-source"
bash "$HOME/MundsenAgent-source/installer/install.sh" "$HOME/MundsenAgent"
```

En versjonert, selvutpakkende installasjonspakke skal erstatte kloningen når
den isolerte pilotporten er bestått. `main` er fortsatt bare en utviklingskilde.

Kildeklonen og runtimekatalogen skal aldri være samme katalog. Installeren
avviser runtime under en Git-arbeidskopi, umerkede kataloger som allerede
inneholder filer, og en ugyldig Mundsen Agent-rotmarkør. Dermed kan instans,
konfigurasjon og provider-auth ikke havne i kildekoderepoet ved et uhell.

## Implementert installasjonsflyt

Installeren:

1. Kontrollerer operativsystem, CPU-arkitektur, diskplass, `curl` og `tar`.
2. Krever en tom eller tidligere merket Mundsen Agent-rot og setter modus `0700`.
3. Legger Mundsen Agent i en versjonert releasekatalog.
4. Installerer administrert Python 3.12 lokalt.
5. Starter de offisielle installasjonene av Claude Code og Codex CLI i et tomt,
   allowlistet miljø med egne HOME-, XDG- og credentialkataloger.
6. Spør om assistentnavn, språk, tidssone, tone, teknisk nivå og agentvalg.
7. Oppretter personlig instans uten å overskrive eksisterende filer.
8. Leser Telegram-token skjult, verifiserer boten og parer nøyaktig én privat
   chat med en tilfeldig kode.
9. Starter interaktiv abonnementsinnlogging for valgt agent.
10. Tilbyr LaunchAgent på macOS eller systemd-brukertjeneste på Linux.
11. Sender bare velkomstmelding etter et eget bekreftelsesspørsmål.

Installer skal kunne kjøres på nytt. Eksisterende personlige filer skal
beholdes. En eksisterende release med samme versjon må bestå sin lagrede
integritetssjekk og blir aldri stilletiende overskrevet eller godkjent på nytt.
Valgfri Lima-installasjon dokumenteres som en separat profil og skal ikke gjøre
native maskinvare til et indirekte krav.

## Installerte stier

| Innhold | Sti under valgt rot | Rettighet |
|---|---|---|
| Mundsen Agent-releases | `releases/<versjon>` | `0700` |
| Aktiv release | `current` | atomisk symlink |
| Personlig instans | `instance/` | `0700` |
| Runtimekonfig | `config/runtime.env` | `0600` |
| Hemmeligheter | `config/secrets.env` | `0600` |
| State og logger | `state/` | `0700` |
| Claude-runtime og auth | `providers/home/` | `0700` |
| Codex-runtime og auth | `providers/bin/`, `providers/codex/` | `0700` |
| Python og verktøy | `tools/` | `0700` |

## Personlig instans

Førstegangsinstallasjonen oppretter:

```text
<Mundsen Agent>/instance/
├── instance.yaml
├── AGENTS.md
├── CLAUDE.md
├── MEMORY.md
├── TODO.md
├── reminders.md
├── memory/
│   ├── user_profile.md
│   ├── open-threads.md
│   └── setup-wishlist.md
└── skills/
```

Ved setup opprettes også administrerte providerlenker:

```text
<Mundsen Agent>/providers/home/.claude/skills/
<Mundsen Agent>/providers/home/.agents/skills/
```

De peker til Mundsen Agents bundled skills og eventuelle private skills i
`instance/skills/`. Mundsen Agent avviser navnekollisjoner og rører ikke urelaterte
provider-skills.

Ingen fil skal inneholde fakta om en ekte person før brukeren eller assistenten
selv legger dem til lokalt.

## Hemmelighetsmal

Minimumsfelt:

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Det offentlige repoet trenger ikke token for update discovery. En valgfri
GitHub-credential for en privat fork legges i samme private fil som
`MUNDSEN_GITHUB_TOKEN`, og må tilhøre brukeren selv.

Ikke-hemmelige valg ligger i `config/runtime.env`:

```dotenv
MUNDSEN_MODULES=
MUNDSEN_UPDATE_CHECK=1
MUNDSEN_UPDATE_REPOSITORY=powermundsen/MundsenAgent
```

`MUNDSEN_MODULES` er tom som standard. Se [local-health](local-health.md) for
eksplisitt aktivering av lokal helseimport.

Moduler legger egne felter til en dokumentert modulfil. Installer skal aldri
skrive en hemmelig verdi til terminaloutput.

## Verifisering

En vellykket installasjon skal vise:

- aktiv, verifisert Mundsen Agent-versjon
- gyldig instansskjema
- private filrettigheter
- tilgjengelig valgt agent-CLI
- Telegram API tilgjengelig
- LaunchAgent aktiv på macOS eller systemd-brukertjeneste aktiv på Linux
- health check med tilgang bare til eksplisitt konfigurerte kataloger

En uoppfordret velkomstmelding sendes bare etter et eget bekreftelsesspørsmål.
Uten godkjenning starter førstesamtalen først når brukeren selv skriver.

## Sikker testgrense

En endret `HOME` er ikke alene en sikker installasjonstest. Et
leverandørinstallasjonsprogram kan fortsatt finne og endre globale verktøy via
`PATH` eller andre arvede miljøvariabler. Mundsen Agent kjører derfor begge
leverandørinstallasjonene med `env -i` og en liten allowlist.

Utviklere skal aldri kjøre disse installasjonene mot en bruker som allerede
driver en assistent. Simulerte tester kan kjøres lokalt. Ekte installasjon,
oppdatering og avinstallering skal testes i en disponibel OS-bruker eller VM
uten tilgang til eksisterende runtime, konfigurasjon, minner eller auth.

## Avinstallasjon

Avinstallasjon skal skille mellom:

- stopp og fjerning av runtimekode
- valgfri eksport eller bevaring av personlig instans
- eksplisitt sletting av secrets, state, logger, cache og backups
- fjerning av eventuell lokal read-only GitHub-tilgang

Personlig instans skal aldri slettes som en implisitt del av avinstallasjon.
