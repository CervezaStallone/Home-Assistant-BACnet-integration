# Privacy policy — BACnet IP integration for Home Assistant

**[English](#english)** · **[Nederlands](#nederlands)**

Last updated: 24 October 2026. Applies to the opt-in usage statistics, available from version 1.0.51-beta.1.

---

## English

### In short

The integration only sends data when you tick **Share anonymous usage statistics**. It is off by default. When it is on, Home Assistant sends one small report a day about your BACnet devices: manufacturer, model, firmware and a few counts. It never sends addresses, names or values. The reports help decide which devices to test and support.

### Who is responsible

BRDC ([brdc.nl](https://brdc.nl)), the maintainer of this integration, is the controller. Questions and requests: [support-ha-bacnet@brianrodriguez.nl](mailto:support-ha-bacnet@brianrodriguez.nl).

### What is sent

While at least one device has the option ticked, one report a day goes to `ha-bacnet.telemetry.brdc.nl`. Only ticked devices are included, and only once they have answered a poll.

| Field | Example | Meaning |
|---|---|---|
| `schema` | `1` | Report format version |
| `install_id` | `3f2b…` | A random ID the integration creates once. Not derived from your Home Assistant or your network |
| `integration_version` | `1.0.51-beta.1` | Version of this integration |
| `ha_version` | `2026.10.1` | Version of Home Assistant |
| `vendor` | `Siemens` | The device's `vendorName` |
| `model` | `PXC200-E.D` | The device's `modelName` |
| `firmware` | `V6.00.338` | The device's `firmwareRevision` |
| `connection` | `direct` | `direct`, or `foreign_device` when connected through a BBMD |
| `cov` | `true` | Whether at least one COV subscription works |
| `rpm` | `true` | Whether the device accepts ReadPropertyMultiple |
| `objects` | `{"0": 12, "2": 40}` | Number of imported objects per BACnet object type |

**Never sent:** IP or BACnet addresses, device instance numbers, object or device names, descriptions, values, or anything else about your Home Assistant setup.

### What the server adds

- The **country** your report came from (a two-letter code). It is looked up in a local database on our own server; no third party is contacted.
- The **date** the report arrived (no time of day), and the dates an installation and a device model were first and last seen.

### Your IP address

Your IP address is used only while your report is received: to look up your country (in a local database, no third party) and for rate limiting. It is never stored. Rate limiting uses a keyed hash of your IP address, not the address itself, which is deleted automatically within one hour. The web server for this address keeps no access logs.

### Why, and on what basis

To learn which BACnet devices and object types are in use, so testing and support go where they matter. The only thing that ever becomes public is a device's manufacturer, model and firmware in the [tested devices](README.md#tested-devices) list, after the maintainer has checked it. The legal basis is your consent (GDPR art. 6(1)(a)): ticking the option.

### Pseudonymous, not anonymous

We cannot see who you are. But the installation ID stays the same, so a report could in theory be linked to a site, for example through a rare combination of device and country. That is why we treat the reports as personal data.

### How long

Reports are kept indefinitely, to follow device support over time.

### Where

On servers in the EU, run by a hosting provider that processes the data on our behalf. No other parties receive it.

### Stopping, access and deletion

- **Stop:** untick the option in each device's options (**Settings → Devices & services → BACnet IP → Configure**). No further reports are sent.
- **Access or deletion:** we do not know which installation ID is yours, so we cannot find your data on our own. Send us your installation ID and we will send you the stored reports or delete all of them. Deletion removes everything stored under that ID, which is what the maintainer's statistics are built from. A manufacturer, model and firmware that is already in the public [tested devices](README.md#tested-devices) list stays there, as does the server's list of known models (when a model was first and last seen): neither contains your installation ID. You find the ID in your Home Assistant configuration folder, in the file `.storage/bacnet.telemetry`, under `install_id`. Do not post it in a public GitHub issue.
- **Complaints:** you can contact the data protection authority in your country. In the Netherlands that is the Autoriteit Persoonsgegevens.

### Changes

Changes to this policy are made in this file and are visible in its history on GitHub. New kinds of data are only collected after this policy has been updated.

---

## Nederlands

### In het kort

De integratie stuurt alleen gegevens als je **Anonieme gebruiksstatistieken delen** aanvinkt. Standaard staat dat uit. Staat het aan, dan stuurt Home Assistant één keer per dag een klein rapport over je BACnet-apparaten: fabrikant, model, firmware en een paar aantallen. Nooit adressen, namen of waarden. De rapporten helpen bepalen welke apparaten getest en ondersteund worden.

### Wie is verantwoordelijk

BRDC ([brdc.nl](https://brdc.nl)), de maker van deze integratie, is verwerkingsverantwoordelijke. Vragen en verzoeken: [support-ha-bacnet@brianrodriguez.nl](mailto:support-ha-bacnet@brianrodriguez.nl).

### Wat er verstuurd wordt

Zolang bij minstens één apparaat de optie aan staat, gaat er één rapport per dag naar `ha-bacnet.telemetry.brdc.nl`. Alleen aangevinkte apparaten gaan mee, en pas als ze op een poll hebben geantwoord.

| Veld | Voorbeeld | Betekenis |
|---|---|---|
| `schema` | `1` | Versie van het rapportformaat |
| `install_id` | `3f2b…` | Een willekeurige ID die de integratie één keer aanmaakt. Niet afgeleid van je Home Assistant of je netwerk |
| `integration_version` | `1.0.51-beta.1` | Versie van deze integratie |
| `ha_version` | `2026.10.1` | Versie van Home Assistant |
| `vendor` | `Siemens` | De `vendorName` van het apparaat |
| `model` | `PXC200-E.D` | De `modelName` van het apparaat |
| `firmware` | `V6.00.338` | De `firmwareRevision` van het apparaat |
| `connection` | `direct` | `direct`, of `foreign_device` bij een verbinding via een BBMD |
| `cov` | `true` | Of minstens één COV-abonnement werkt |
| `rpm` | `true` | Of het apparaat ReadPropertyMultiple accepteert |
| `objects` | `{"0": 12, "2": 40}` | Aantal geïmporteerde objecten per BACnet-objecttype |

**Nooit verstuurd:** IP- of BACnet-adressen, device instance-nummers, object- of apparaatnamen, beschrijvingen, waarden, of iets anders over je Home Assistant-installatie.

### Wat de server toevoegt

- Het **land** waar je rapport vandaan kwam (een code van twee letters). Dat wordt opgezocht in een lokale database op onze eigen server; er wordt geen derde partij benaderd.
- De **datum** waarop het rapport binnenkwam (zonder tijdstip), en de datums waarop een installatie en een apparaatmodel voor het eerst en het laatst gezien zijn.

### Je IP-adres

Je IP-adres wordt alleen gebruikt tijdens het ontvangen van je rapport: om je land op te zoeken (in een lokale database, geen derde partij) en voor rate limiting. Het wordt nooit opgeslagen. Voor rate limiting gebruiken we een versleutelde hash van je IP-adres, niet het adres zelf, die binnen een uur automatisch wordt verwijderd. De webserver voor dit adres houdt geen toegangslogs bij.

### Waarom, en op welke grond

Om te weten welke BACnet-apparaten en objecttypes in gebruik zijn, zodat testen en ondersteuning gaan naar waar het telt. Het enige dat ooit openbaar wordt, is de fabrikant, het model en de firmware van een apparaat in de lijst met [geteste apparaten](README.md#tested-devices), nadat de maker die heeft gecontroleerd. De grondslag is je toestemming (AVG art. 6 lid 1 onder a): het aanvinken van de optie.

### Pseudoniem, niet anoniem

We kunnen niet zien wie je bent. Maar de installatie-ID blijft hetzelfde, dus een rapport zou in theorie aan een locatie gekoppeld kunnen worden, bijvoorbeeld via een zeldzame combinatie van apparaat en land. Daarom behandelen we de rapporten als persoonsgegevens.

### Hoe lang

Rapporten worden onbeperkt bewaard, om de ondersteuning van apparaten door de tijd te volgen.

### Waar

Op servers in de EU, beheerd door een hostingpartij die de gegevens in onze opdracht verwerkt. Geen andere partijen ontvangen ze.

### Stoppen, inzage en verwijderen

- **Stoppen:** vink de optie uit in de opties van elk apparaat (**Instellingen → Apparaten en diensten → BACnet IP → Configureren**). Er worden dan geen rapporten meer verstuurd.
- **Inzage of verwijderen:** we weten niet welke installatie-ID van jou is, dus we kunnen je gegevens niet zelf vinden. Stuur ons je installatie-ID en we sturen je de opgeslagen rapporten of verwijderen ze allemaal. Verwijderen wist alles wat onder die ID is opgeslagen, en daarop zijn de statistieken van de maker gebaseerd. Een fabrikant, model en firmware die al in de openbare lijst met [geteste apparaten](README.md#tested-devices) staan, blijven daar staan, net als de lijst met bekende modellen op de server (wanneer een model voor het eerst en het laatst gezien is): geen van beide bevat je installatie-ID. Je vindt de ID in je Home Assistant-configuratiemap, in het bestand `.storage/bacnet.telemetry`, onder `install_id`. Zet hem niet in een openbaar GitHub-issue.
- **Klachten:** je kunt terecht bij de privacytoezichthouder in je land. In Nederland is dat de Autoriteit Persoonsgegevens.

### Wijzigingen

Wijzigingen in deze verklaring gebeuren in dit bestand en zijn zichtbaar in de geschiedenis op GitHub. Nieuwe soorten gegevens worden pas verzameld nadat deze verklaring is bijgewerkt.
