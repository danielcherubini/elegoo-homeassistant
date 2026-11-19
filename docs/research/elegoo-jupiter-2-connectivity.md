---
status: current
last-verified: 2026-11-19
verified-by: web research — 4 angles, ~40 sources (Elegoo official, FCC, Chitu/CBD-Tech specs, 8 community repos, 6 reviews), 2026-11-19
---

# Elegoo Jupiter 2: connectivity, and why #452 cannot connect

**Question settled:** Does the Elegoo Jupiter 2 exist, how does it communicate over a network, has anyone ever connected one to third-party software, and is "we don't support this printer" the correct diagnosis for [#452](https://github.com/danielcherubini/elegoo-homeassistant/issues/452)?

Companion: [elegoo-jupiter-external-software-survey.md](elegoo-jupiter-external-software-survey.md) — the full third-party integration survey, including the auditable search surface behind the "nobody has integrated the Jupiter 2" negative.

## Executive summary

The Jupiter 2 ships and is Wi-Fi + USB only. It is **not a Chitu board**, unlike every Elegoo resin printer this ecosystem has previously integrated. Nobody anywhere has published a working network integration for it. But the diagnosis in #452 is **not** "unsupported model" — this integration has no model-rejection path at all — and the README appears to be what led the reporter to try in the first place.

| Question | Answer |
|---|---|
| Does it exist? | Yes. **ELEGOO Jupiter 2**, large-format MSLA resin, debuted 2025-04-08, launched 2026-04-15, $949 MSRP. |
| Connectivity? | **Wi-Fi + USB only.** No Ethernet, no SD card. |
| Local network control? | Yes, officially — SatelLite "Network Transmission". **But it is not SDCP, and nobody has documented what it is.** |
| Anyone connected one? | **No.** Zero working implementations, zero published captures. |
| Is "unsupported model" the diagnosis? | **No.** This codebase cannot reject an unknown model. It is a connectivity failure. |

## Findings

### 1. The Jupiter 2 is not a Chitu board — every prior resin success assumed it was

This is the load-bearing finding, because every existing resin integration in the ecosystem is built on Chitu's SDCP.

**The original Jupiter (2021) is provably Chitu**, from Elegoo's own repository: it ships a file literally named `Jupiter ChiTu Mainboard Specification20220210.gcode` containing Chitu's parameter block (`M8513`, `M8004`/`M8005`, `M8006 I18`) — [elegooofficial/Jupiter](https://github.com/elegooofficial/Jupiter/tree/main/Jupiter%20Firmware). Elegoo's Jupiter FAQ says the machine "is powered by the latest Chitubox motherboard".

**The Jupiter 2's mainboard carries no Chitu branding.** FCC internal photos for `2A5HX-JUPITER2` (filed 2025-08-19) show an **ELEGOO-silkscreened** mainboard, with the radio/SoC daughterboard marked **`FALSC_A04_V1.2`** carrying a **WilnaX WA172A** Wi-Fi/BLE SoC and ESMT DRAM — a Linux-class SoC design, not the STM32+FPGA of Chitu's L/E series ([fccid.io/2A5HX-JUPITER2](https://fccid.io/2A5HX-JUPITER2), internal photos 31–34, 19–20). *INFERRED from board topology; the SoC die marking is not fully legible at 600 dpi.*

Corroborating, and independent of the photos: **Chitu's own store lists replacement controller boards for Mars, Saturn, Jupiter SE and Saturn 4 Ultra — but not Jupiter 2** ([chitusystems.com](https://www.chitusystems.com/collections/chitu-chitu-controller-board)). A vendor that sells boards for the previous three models not selling one for this one is consistent with Elegoo having moved off Chitu hardware.

### 2. Resin was never the blocker; the Jupiter 2 specifically is

The premise that Elegoo resin printers can't be networked is false, and the distinction matters for sizing any future work.

Third-party network integrations for Elegoo **resin** exist repeatedly, from independent authors, since 2023 — `vvuk/cassini` (SDCP V1, "no encryption or anything, and the protocol is fairly simple"), `nakomis/cthulhu` (Mars 5 Ultra, includes a `fake-printer` built from real captures), `arsi-apli/UltraRemote` (client *and* patched firmware), `alfiedennen/sdcp-saturn-4-ultra`, plus ChitUI, mariner, RPP, ResinPilot and this integration. Full detail in the companion survey.

**For the Jupiter 2 the record is empty.** No published capture, no working client, no protocol write-up. Two open issues ask the same question and both have **zero replies** — [#452 here](https://github.com/danielcherubini/elegoo-homeassistant/issues/452) and `Open-Resin-Alliance/df-plugin-elegoo` [#18](https://github.com/Open-Resin-Alliance/df-plugin-elegoo/issues/18). Two independent people tried and got nothing back. **The reporter in #452 is not doing anything wrong**, and that is worth saying to them.

Two further silences corroborate: Elegoo's own Apache-2.0 LAN SDK [`elegooofficial/elegoo-link`](https://github.com/elegooofficial/elegoo-link) lists Centauri Carbon, Centauri Carbon 2, Neptune 4 Pro/Plus/Max, OrangeStorm Giga and "other Moonraker printers" — **no resin printer at all**, and its `PrinterType` enum contains only FDM values. And [`PtolemyDL/ResinPilot`](https://github.com/PtolemyDL/ResinPilot), a maintained per-model support matrix, lists "Jupiter, Jupiter SE (networked units)" as SDCP V1 and **omits Jupiter 2 entirely** — someone maintaining exactly this table looked at the Jupiter line and did not place the J2.

### 3. Official connectivity: Wi-Fi + USB, with a real local path that is not SDCP

**VERIFIED from Elegoo's own press-release spec table** ([PR Newswire, 2026-04-15](https://www.prnewswire.com/news-releases/elegoo-launches-large-format-resin-printer-jupiter-2-setting-new-benchmark-in-lcd-3d-printing-302740587.html)) and the [US store comparison table](https://us.elegoo.com/products/elegoo-jupiter-2):

| Field | Value |
|---|---|
| Connectivity | Wi-Fi, USB |
| File Transfer | Wi-Fi, USB, SatelLite |
| User Interface | Touch Screen, Matrix, SatelLite |
| OTA Firmware Upgrade | Yes |

**No Ethernet, no SD card** — established by absence across the official spec table, the store comparison table, every Jupiter 2 wiki page (grepped for `ethernet|RJ45|网线`, zero hits), the wiki page index, Tom's Hardware's spec table and PCMag. ⚠️ **Do not carry forward the fact that the *original* Jupiter had an Ethernet port** — that was the 2021 model, and its port was widely reported as non-functional anyway.

**Local LAN sending is official and does not require an account.** Elegoo documents the procedure — printer on the LAN, note the "Network address" in Device Info, SatelLite → `Device → Add Device` → slice → **`Network Transmission`** → `Send Files` ([wiki.elegoo.com](https://wiki.elegoo.com/lcd-slicing-software/elegoo-satellite-chitubox-how-to-print-via-lan)). Elegoo's [privacy agreement](https://www.elegoo.com/pages/privacy-agreement) is explicit: *"LAN mode: If you and the printer are on the same local network, slice files… will be transmitted directly from your computer to the printer, without passing through our cloud server"*, and *"SatelLite currently only supports LAN point-to-point transmission"*. Fabbaloo's hands-on confirms it from the field — and confirms the limit: *"only when we were on our local network"*.

**So a local path exists. It is simply not SDCP, and nobody has documented what it is.**

**The Matrix app is cloud, not local.** It requires an account (phone number + verification code) and binds by QR code from `Device Info → Account`; Elegoo's own FAQ requires the printer be "connected to the internet" for remote use, and the Play listing advertises "Cloud Sync". For the Jupiter 2 specifically, Matrix prints **files already resident on the printer** (`Select a file from the printer's local files or print history`) — direct cloud file upload is a newer feature that Elegoo's July 2026 announcement scopes to **Centauri Carbon 2 / Combo only**. Do not claim the Jupiter 2 supports cloud upload.

Two constraints that bear directly on protocol expectations:

- **SatelLite only, and no `.ctb`.** Official FAQ: *"The ELEGOO Jupiter 2 currently supports only ELEGOO SatelLite"* and *"does not support the .ctb file format"*. SDCP's own `SupportFileType` example value is `CTB`. A printer that rejects CTB is not a normal SDCP resin endpoint. (Chitubox added a J2 slicing profile in v1.3.0, 2026-07-09, but community reports say **slicing only, not network sending**.)
- **Connection contention is real and shipped on this model.** Jupiter 2 firmware `V01.00.02.13` changelog: *"Fixed a bug where the SatelLite network would freeze when connected to the app."* That is client contention — the same class our own README already warns about for FDM printers (4 simultaneous connections). Any future integration must expect to compete for slots.

### 4. The README appears to have caused #452, and "unsupported model" is not a failure this code can produce

`README.md:70` lists **"Jupiter Range"** under "Legacy Printers (SDCP over MQTT)". **Nothing in the source backs that claim** — `grep -rin jupiter custom_components/` returns zero hits: no keyword, no test, no model-specific handling. `git log --all -i --grep=jupiter` returns zero commits. The resin classifier keywords are `mars` and `saturn` only (`sdcp/models/enums.py:549-552`), so a Jupiter classifies as `printer_type = None` (`:562`).

That claim is very plausibly **why #452 was filed**: a reader saw "Jupiter Range", tried their Jupiter 2, and it failed.

**And the diagnosis people would reach next is wrong.** In this codebase, an unsupported model is not a failure mode that exists:

- Transport is chosen from the **printer's own `ProtocolVersion` string**, never from its model name (`sdcp/models/printer.py:207-210`; `enums.py:40-60` — `V1` → MQTT, everything else → V3/WebSocket; CC2 is set by *which UDP port answered*, `cc2/discovery.py:85-86`).
- `PrinterType.from_model` returning `None` is **never inspected** by `config_flow.py`. An unrecognised model is accepted and routed to `async_step_fdm_options()` (`config_flow.py:1405-1410`).
- There is **no "unsupported model" error key** in `translations/en.json` at all.

**Therefore adding `"jupiter"` to the keyword list would change which entities appear and would not make the printer connect.** There is no model registry to add an entry to.

`No printer found at IP address` (`config_flow.py:1378`) is consequently a pure identification failure after all three attempts returned nothing: no `M99999` answer on UDP 3000 (`const.py:46-47`), no `{"id":0,"method":7000}` answer on UDP 52700 (`cc2/const.py:8-9`), and either no TCP 3030 listener or one that never emitted a non-empty `attributes` frame carrying a usable `MainboardID` (`config_flow.py:883-897`, `:970-971`).

### 5. The most valuable unknown is already instrumented

The code **already distinguishes the two cases that determine the size of this problem**, at DEBUG level:

- `config_flow.py:914` — *"Nothing at %s:%s could be read over the live connection"* (`connected=False`) → the Jupiter 2 is **silent** on our ports. A genuinely unknown protocol; a large project, and it needs a capture before any design work is possible.
- `config_flow.py:921` — *"Nothing at %s:%s described itself: no attributes frame was received"* (`connected=True`) → something **is** listening on 3030 and speaks enough WebSocket to hand over a frame, but never a usable `attributes` frame. A far smaller gap, potentially a frame-shape or timing difference.

The reporter's log in #452 contains only the collapsed `WARNING` at `:1378`, which erases this distinction. **One debug-log request answers it.** Until that answer exists, any design for Jupiter 2 support is speculation.

## Evidence

**Primary / official (highest credibility):** Elegoo press-release spec table (PR Newswire); us.elegoo.com product + comparison tables; wiki.elegoo.com Jupiter 2 FAQ, "How to Print via LAN", Matrix connection guide; elegoo.com privacy agreement; `elegooofficial/Jupiter`, `elegooofficial/elegoo-link`, `elegooofficial/GOO`; FCC 2A5HX-JUPITER2 internal photos; [CBD-Tech SDCP v3.0.0 spec](https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/blob/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md); Chitu Systems board catalogue; Chitubox version history.

**Community code (high credibility — measured, not asserted):** `vvuk/cassini`, `nakomis/cthulhu`, `ElMougo/df-plugin-sdcp-v1`, `connorslade/mslicer`, `alfiedennen/sdcp-saturn-4-ultra`, `PtolemyDL/ResinPilot`, `ChestnutLabs/printer-protocol-orchard`.

**Reviews (secondary, corroborating):** Tom's Hardware, PCMag, Fabbaloo, FauxHammer, 3D Printing Industry, TCT Magazine.

**Local code:** `custom_components/elegoo_printer/` at `74fe85e` (v2.16.0-beta.4), read-only.

**Credibility note on `glococo` in UVtools #1114:** opened the firmware, reported *"openwrt/tina linux running on allwinner r528… For me, it is a Dead end."* Single source, high technical credibility, but about the firmware/`.goo` side rather than the LAN API specifically. Strong evidence of difficulty, not proof of impossibility.

## Unresolved contradictions

1. **"Chitu" per owners vs ELEGOO silicon per FCC.** A Reddit owner states "the chitu motherboard the machine uses is a 32 bit architecture". The FCC photos show no Chitu branding. Most likely either Chitu-derived *firmware* on Elegoo-branded silicon, or an owner generalising from the Jupiter SE. Unresolvable without a firmware dump. This one matters: if Chitu firmware is running, SDCP might be present but gated.
2. **A local path is officially documented, yet nobody has captured it.** SatelLite's `Network Transmission` proves the J2 listens for something on the LAN. The absence of any capture of a protocol that demonstrably works is a gap in community effort, not evidence the protocol is unreachable.

## Gaps

1. **Which of `:914` vs `:921` fires for the reporter** — the decisive unknown, answerable with one debug-log request.
2. **No Jupiter 2 port, discovery-string or auth capture exists anywhere.** The negative is bounded: Discord and Chinese-language forums, where Chitu reverse-engineering is most active, were not searchable.
3. **Elegoo's Jupiter 2 manual PDF could not be read** — a single-page 12844×572pt Illustrator vector export with no text layer, and no OCR available. The manual's own connectivity section and port diagram are unverified; the port inventory rests on spec tables plus two independent reviews.
4. **Whether SDCP v3 works on the J2 but is gated** behind a firmware version or Matrix pairing step — plausible, untested.
5. **Which `.goo` dialect the J2 accepts** — its 15120×6230 panel exceeds the resolution in published GOO spec examples.
6. Whether connection contention persists after firmware `V01.00.02.25`.

## Implications for this project (not a plan)

- The README claim at `README.md:70` is unsupported by code and plausibly generated this issue. Narrowing it to what is independently corroborated (Jupiter / Jupiter SE under SDCP V1) is a documentation-accuracy fix, independent of Jupiter 2.
- Jupiter 2 support is **not specifiable today**: there is no protocol to target. The prerequisite is a capture, and the cheapest route to one is the reporter's debug log.
- If the J2 turns out to speak something new, it is a **third stack** in this codebase rather than an extension — the FDM line already spans three mutually unrelated protocols (Moonraker :7125, SDCP :3030, CC2 inverted-MQTT :1883), and CC2 required its own package and config-flow route.
