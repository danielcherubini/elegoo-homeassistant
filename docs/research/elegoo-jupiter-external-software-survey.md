---
status: current
last-verified: 2026-11-19
verified-by: web research — GitHub, forums, UVtools issue thread, 2026-11-19
---
> Companion to [elegoo-jupiter-2-connectivity.md](elegoo-jupiter-2-connectivity.md) — this file is the detailed
> survey of third-party Elegoo resin integrations, including the auditable search surface (§7)
> behind its "nobody has integrated the Jupiter 2" negative.
# Third-party network integrations for Elegoo resin printers

A survey of every documented attempt to talk to an Elegoo resin printer from outside the official
app — the evidence base for whether the Jupiter 2 is reachable, and what has worked elsewhere.


**Research date:** 2026-11-19
**Question:** Is there any documented instance of talking to an Elegoo Jupiter / Jupiter 2 from outside the official app (Elegoo Matrix / SatelLite / Chitubox)?
**Short answer:** **Yes for the original Jupiter (and Jupiter SE). No for the Jupiter 2 — as of 2026-11-19 nobody has published a working network integration for it, and the one person who tried in this very repo filed an unanswered issue.**

---

## 0. The headline, stated carefully

Three separate claims get conflated. Keeping them apart is the whole answer:

| Claim | Verdict | Confidence |
|---|---|---|
| **Has any Elegoo RESIN printer ever been integrated over the network by a third party?** | **YES — repeatedly, by multiple independent authors, since 2023.** Resin is *not* the blocker. | VERIFIED |
| **Has the ORIGINAL Jupiter (6K, 2021) been integrated?** | **YES — it speaks SDCP V1 over MQTT**, same as Saturn 2/3 and Mars 3/4. Multiple clients list it. | VERIFIED (protocol class), INFERRED (per-unit Jupiter testing) |
| **Has the JUPITER 2 been integrated?** | **NO.** Zero working implementations found anywhere. One failed attempt, unanswered. | VERIFIED (absence, with search surface documented in §7) |

The existence proof the question asks for — "has *anyone* ever done this for an Elegoo resin machine" — is satisfied many times over. The gap is specifically Jupiter 2.

---

## 1. The resin existence proof (VERIFIED)

These are independent third-party projects, all talking to Elegoo **resin** printers over the LAN with no official app involved.

### 1a. `vvuk/cassini` — the original reverse-engineering (2023, MIT)
- **URL:** https://github.com/vvuk/cassini
- **Author:** Vladimir Vukicevic (`vvuk`, ex-Mozilla, Firefox Reality / Servo)
- **Date:** repo created 2023-09; announced on Reddit 2023-11
- **Status:** WORKS — "only been tested with a Saturn 3 Ultra, but may work with other ChiTu-mainboard printers with WiFi." Last push 2024; effectively frozen but functional.
- **Protocol:** ELEGOO **SDCP V1 over MQTT**, with **UDP discovery**. Ships `simple_mqtt_server.py` + `simple_http_server.py` — i.e. the *client hosts the broker* and the printer dials in.
- **Why it matters:** cassini is the Ur-text. Every subsequent Elegoo resin client — including the integration in this repo — descends from or cross-references it. Vukicevic's own line in the README: "Turns out there's no encryption or anything, and the protocol is fairly simple."
- **Announcement threads:** r/ElegooMars 2023-11 (`169b9rf`), r/ElegooSaturn (`13yc4kd`).

### 1b. `arsi-apli/UltraRemote` — Java CLI, Saturn 3 Ultra (2023-09 → 2024-02)
- **URL:** https://github.com/arsi-apli/UltraRemote (47★)
- **Status:** WORKS, then abandoned (last push 2024-02-18)
- **Protocol:** SDCP V1/MQTT over WiFi; status, upload, start print. Ships a modified-firmware companion `UltraFirmwareToolkit` adding Windows share + SSH to the printer itself — i.e. someone got a shell on an Elegoo resin printer's Linux.
- **Note:** This is the *deepest* resin-side hack found: firmware modification, not just protocol use.

### 1c. `gokuhs/ElegooRemoteControl` — C++ port of cassini (GUI)
- **URL:** https://github.com/gokuhs/ElegooRemoteControl
- **Status:** works (build-it-yourself); explicitly credits cassini's reverse-engineered logic, MIT.

### 1d. `jjtronics/RPP` + `MichielBruijn/Resin-Print-Portal` — web UI forks of cassini
- **URLs:** https://github.com/jjtronics/RPP · https://github.com/MichielBruijn/Resin-Print-Portal
- **Status:** works; "web interface for ELEGOO resin printers over WiFi (SDCP), fork of jjtronics/RPP built on Cassini". Implements the Elegoo SDCP protocol directly.

### 1e. `jangrewe/ChitUI` (and forks `xmodpt`, `antoinebou12`) — web UI for SDCP 3.0 resin printers
- **URL:** https://github.com/jangrewe/ChitUI
- **Status:** works; actively used. Targets SDCP 3.0 (`ws://<ip>:3030`), documented from **personal pcaps** (author states in r/ElegooSaturn `1fxl34s`: "Port 3030 is the Websocket server over which the whole API communication happens (which i'm already using)"). Also confirms RTSP on 554.
- **This is a genuine independent capture/proxy effort, not a spec read.**

### 1f. `amd989/mariner` / `mariner3d/mariner` — MSLA print server
- **URL:** https://github.com/mariner3d/mariner
- **Status:** works; "NEW ChiTuBox / SDCP 3.0.0 support", "Mariner speaks SDCP v3.0.0, so ChiTuBox discovers and drives it as if the printer had network support built in". Its predecessor `rudetrooper/Octoprint-Chituboard` was the **OctoPrint** route (§2).

### 1g. `nakomis/cthulhu` — standalone print server, Mars 5 Ultra (2026-09 → 2026-11)
- **URL:** https://github.com/nakomis/cthulhu
- **Status:** WORKS, actively developed. The single best-documented independent resin protocol write-up found.
- **Explicitly rebuts the "you can't do this" premise** — from its README:
  > "In FDM printing, you're driving G-code over a serial link to Marlin-style boards — OctoPrint is the way to go. A resin printer is a very different beast; it's a layer-image machine with a Chitu mainboard, **so there is nothing for OctoPrint to talk to**. What the printer *does* expose is **SDCP** (Smart Device Control Protocol) over the LAN — and that is enough to do the bits we really need."
- **Protocol details it contributes:** three fields are **misspelled on the wire** and must be sent verbatim — `CurrenCoord`, `RelaseFilmState`, `MaximumCloudSDCPSercicesAllowed`. Ships a `fake-printer` package built from real captures.

### 1h. `alfiedennen/sdcp-saturn-4-ultra` — real-device SDCP V3 notes (2026-07)
- **URL:** https://github.com/alfiedennen/sdcp-saturn-4-ultra
- **Status:** documentation, verified on a live Saturn 4 Ultra 16K (fw V1.5.6).
- **Why it matters:** the authoritative "what actually works on resin" table. Notably:
  - Cmd 132 (`StopMaterialFeeding`) and 133 (`SkipPreheating`) return **silence, not an Ack** on resin — they are FDM concepts. This is a *spec divergence* from the cbd-tech V3 spec.
  - Cmd 260 (`RetrieveFileInfo`) silent → no file size over SDCP on resin.
  - Upload ~170 KB/s over onboard WiFi; RTSP 554 single-client.
  - Documents the same video-slot leak that this repo tracks as issue #451.

### 1i. `pollstar/ha-sdcp` — a second, independent **Home Assistant** integration for SDCP printers
- **URL:** https://github.com/pollstar/ha-sdcp (+ Lovelace card `pollstar/sdcp-printer-card`)
- **Date:** 2026-11-03, single-commit, 0★ — very new, unproven.
- **Status:** UNKNOWN / likely works for Saturn 4 Ultra; not hardware-verified beyond that.
- **Protocol:** plain SDCP v3, `ws://<printer>:3030/websocket`, UDP `M99999`→3000.
- **⚠️ Important caveat:** its README **claims Jupiter** in the model list, but the `custom_components/sdcp/printers/` directory contains **only two registered models — `elegoo-saturn-4-ultra` and `elegoo-centauri-carbon`.** Jupiter falls through to the `generic-resin` fallback. So the Jupiter mention is aspirational prose, **not** a implemented/verified path. Treat as NOT proof.

### 1j. `PtolemyDL/ResinPilot` — self-hosted resin fleet manager (2026)
- **URL:** https://github.com/PtolemyDL/ResinPilot
- **Status:** works; explicitly hardware-tested on Saturn 4 Ultra 16K / Saturn 4 Ultra / Mars 5 Ultra.
- **The most useful taxonomy found**, because it separates the two SDCP generations:
  - **SDCP 3** (WebSocket, printer is the server) — supported today.
  - **SDCP 1** (MQTT, *"where the printer connects to a message server (MQTT) run by the app"*) — **"Found, not supported yet… won't add them until an MQTT transport is written."**
  - Under **"🟡 Found, not supported yet"** it lists: Saturn 2/2 8K, Saturn 3, Saturn 3 Ultra, Mars 3/3 Pro/4/4 DLP/4 Max/4 Ultra, **and "Jupiter, Jupiter SE (networked units)"**.
- **This directly confirms: original Jupiter = SDCP V1/MQTT.** That is a real, published, third-party statement of Jupiter's transport class.

### 1k. `Open-Resin-Alliance/df-plugin-sdcp-v3` — SDCP V3 plugin for the DragonFruit open slicer
- **URL:** https://github.com/Open-Resin-Alliance/df-plugin-sdcp-v3 (2026-11-14)
- **Status:** new; part of the 92★ DragonFruit open-source resin slicer.
- **Relevance:** the open-source resin ecosystem is actively building SDCP send/print, so this will keep improving.

---

## 2. OctoPrint / Moonraker / Klipper (VERIFIED negative, with nuance)

### OctoPrint — historically yes, but by the *wrong* channel
- **`rudetrooper/Octoprint-Chituboard`** — https://github.com/rudetrooper/Octoprint-Chituboard
- **Status:** works-ish, effectively abandoned. Listed printers: **Elegoo Mars, Mars Pro, Mars 2, Mars 2 Pro, Elegoo Saturn** — **no Jupiter**.
- **How it works — and this is the key point:** it does **not** use the network. It connects a Raspberry Pi to the **Chitu mainboard's UART** physically ("Connect the jumper wires from the pi's UART0 port to the Elegoo Mars 2 motherboard"). Requires a Pi with USB-OTG / GPIO UART. It is a *serial* bridge, not a network protocol client.
- **Known breakage:** "Plugin might not work if you've updated your Elegoo Mars printer to the newest firmware due to issues with Chitu3d encrypting their files."
- **Verdict:** OctoPrint is structurally the wrong tool for resin (no G-code, no serial motion protocol in the useful sense). `cthulhu`'s README states this plainly. **Nobody has ever spoken SDCP *from* OctoPrint's network stack to a Jupiter.**

### Moonraker / Klipper — **never, and structurally impossible**
- Elegoo's own Klipper/Moonraker printers are the **FDM** Neptune 4 family and OrangeStorm Giga (`elegooofficial/Klipper`, `neptune3*`, `OrangeStormGiga` repos).
- **No Elegoo resin printer runs Klipper/Moonraker.** The Chitu board runs a closed firmware; the Jupiter 2 runs a different Linux entirely (§4).
- Elegoo's **own first-party LAN SDK, `elegooofficial/elegoo-link` (Apache-2.0, created 2026-01-12)**, is the strongest evidence: its `PrinterType` enum is
  ```
  UNKNOWN=-1, ELEGOO_FDM_KLIPPER=0, ELEGOO_FDM_CC=1, ELEGOO_FDM_CC2=2, GENERIC_FDM_KLIPPER=100
  ```
  **Every value is FDM.** README "Supported Printer Models": Centauri Carbon, Centauri Carbon 2, Neptune 4 Pro/Plus/Max, OrangeStorm Giga. **No resin model at all.** Elegoo's own SDK has no resin adapter.
- **Verdict:** Moonraker/Klipper for a Jupiter is not a thing and will not become one.

---

## 3. Direct protocol work — what is actually known (VERIFIED)

### The published spec
- **`cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0`** — https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0 (Chitu/CBD-Tech, pushed 2024-06). **V3 only.** There is **no official published V1 spec** — V1/MQTT was reverse-engineered by the community (cassini).
- Chitubox marketing page: https://www.chitubox.com/en/article/news/sdcp3.0 — "SDCP will initially be implemented in the latest ELEGOO Saturn 4 Ultra."

### The wire format (consolidated from cassini, cthulhu, alfiedennen, ChestnutLabs)

**SDCP V3 (WebSocket)** — *modern resin: Saturn 4 Ultra, Mars 5 Ultra; and FDM Centauri Carbon*
- Discovery: UDP broadcast ASCII `M99999` → **UDP 3000**; reply JSON with `Attributes` + `Status`
- Control: `ws://<ip>:3030/websocket` — plain, **no auth, no TLS, no token** (LAN-trust only)
- Upload: HTTP chunked `POST http://<ip>:3030/uploadFile/upload`
- Camera: `rtsp://<ip>:554/video`
- Envelope: `{Id, Data:{Cmd, Data, RequestID(hex16), MainboardID, TimeStamp, From}, Topic:"sdcp/request/<MainboardID>"}`
- Topics: `sdcp/request|response|status|attributes|error|notice/<MainboardID>`; correlation by echoed `RequestID`
- `From` enum: `0`=PC/LAN, `1`=WEB_PC, `2`=WEB, `3`=APP, `4`=SERVER
- Limits: **5 concurrent WS connections** (6th → HTTP 500), **60 s idle timeout**, no subscribe command (poll `Cmd 0`)

**SDCP V1 (MQTT)** — *legacy resin: Jupiter, Jupiter SE, Saturn 2/3, Mars 3/4*
- Discovery: same UDP `M99999`
- **Inverted topology:** the printer does **not** run a broker. The *client* runs one, and the client sends UDP command **`M66666 <host> <port> [user] [pass]`** telling the printer where to dial in.
- Topics: `/sdcp/request/<mainboardID>`, `/sdcp/response/…`, `/sdcp/status/…`, `/sdcp/attributes/…`
- `Cmd 512` = `SET_STATUS_UPDATE_PERIOD` (`TimePeriod: 5000`) → printer auto-pushes
- `CurrentStatus` arrives as a bare **int** on MQTT vs a **list** on WebSocket
- **MQTT printers send no `TaskId`** → no begin/end time, no cover image (source: this repo's PR #261)

**Third corroborating source:** `ChestnutLabs/printer-protocol-orchard` → `protocols/elegoo.md` — an independent, well-sourced protocol orchard. Its key line: *"there is no common Elegoo protocol"* — three unrelated LAN stacks (Moonraker / SDCP-WS / proprietary MQTT). It also documents the load-bearing hazard: on SDCP, **an unrecognized or incomplete command can crash the printer daemon and kill an active print.**

### Wireshark / nmap / pcap captures that exist publicly
- **ChitUI author (r/ElegooSaturn `1fxl34s`)** — captured the SDCP 3.0 WebSocket traffic himself; also checked whether uploaded .goo/.ctb files carry signing beyond an integrity hash.
- **r/elegoo `1nuq5q6` "ports/protocols (firewalling) documentation?"** — a user nmap'd their printer and posted the observed traffic. Elegoo support reportedly had *"no official documentation or info available."*
- **r/ElegooSaturn `1id51rq`** — reveals the Saturn 4 Ultra camera is actually **RTP/UDP**, discovered via Wireshark, played with `ffplay` + a hand-edited `.sdp`.
- **`HoffmanEngineering/printlog-bridge`** — a real bridge project that sends `M99999` to UDP 3000 and records traces (`trace = true`) from user printers by request.

### The biggest structural fact about Jupiter 2 (VERIFIED, §4)
The Jupiter 2 is **not a Chitu SDCP board**. Its file format diverges and its SoC is different. Any SDCP assumption about it is unfounded.

---

## 4. What the Jupiter 2 actually is — the crux (VERIFIED, from a primary source)

The single most information-dense public source on the Jupiter 2's internals is **`glococo`** in **`sn4k3/UVtools` issue #1114 "Elegoo Jupiter 2 not compatible"** (opened 2026-05-04 by `jimmburk`, 18 comments, closed 2026-07-26).
**URL:** https://github.com/sn4k3/UVtools/issues/1114

### File format: not even the same .goo
- `sn4k3` (UVtools author), 2026-06-11: *"Goo spec has been in Big endianness so far but somehow **Jupiter 2 uses Little endianness**…"*
- Then, same day, deeper: *"Problem is even worse than I thought… **is not only a Endianness change, but the format changes!** Before layer table there are a new table; Layer table change structure; After layer table maybe we have layer RLE; **A checksum at the end of the file.** The format also have new version, from V3.0 which is the version UVtools knows to **V5.1 (Jupiter)**."*
- **The Jupiter 2 does not accept `.ctb` at all** — confirmed by Elegoo's own FAQ (§5) and by `glococo`.
- UVtools added GOO v5.1 support (v6.2) but hand-authored files still fail a checksum/verification; `glococo` 2026-08-19: *"looks like checksum or any other verification"* per `sn4k3`.

### Hardware: not a Chitu board
- **`glococo`, 2026-09-07** — the money quote:
  > *"I opened up the firmware .img and find out it is an **openwrt/tina linux running on allwinner r528**. **Fpga bitstream reside in a partition.** If maybe someone spend enough time, it will probably achieve per-layer feature and even unlock new features. For me, it is a **Dead end** until Elegoo update the firmware."*
- Cross-reference: Elegoo's own `elegooofficial/CentauriCarbon` README says the **CC (FDM)** uses an Allwinner R528 with DSP+MCU. So the Jupiter 2 shares a SoC family with Elegoo's FDM platform, *not* with the Chitu resin boards. That is a strong hint that the Jupiter 2's network stack is closer to the FDM/Elegoo-link world than to SDCP-classic.
- Motherboard part number from Elegoo's own wiki: **A0J181** (https://wiki.elegoo.com/jupiter/jupiter-2-motherboard-replacement).
- **INFERRED (not verified):** if the Jupiter 2's LAN stack resembles the CC2's (printer-run MQTT + access code) rather than SDCP, that would explain every failed attempt. **This is a hypothesis with circumstantial support only — nobody has published a capture proving it.**

### Interoperability: Elegoo's own statements
- **Jupiter 2 FAQ** (https://wiki.elegoo.com/faq/jupiter-2): *"The ELEGOO Jupiter 2 currently supports only ELEGOO SatelLite… does not support the .ctb file format… Yes, the ELEGOO Jupiter 2 supports online slicing and remote printing."*
- Corroborated: r/Chitubox `1tr27hs` — *"As of currently you can only use elegoo satellite slicer to slice and send files to the Jupiter"*; a later comment — *"Latest Chitubox has EJ2 support. **Can Save to disk, not network sending yet.**"*
- Fauxhammer review, Hackster, Fabbaloo, Tom's Hardware, PCMag all independently repeat: SatelLite is the only compatible slicer as tested.

### The Matrix app is cloud-first, not a LAN protocol you can copy
- Matrix supports Jupiter 2, but there is **no LAN-only mode documented for Jupiter 2** the way there is for CC2 (`Settings → LAN Only → Access Code`). Jupiter 2 pairing is via **QR code / PIN in Device Info**, i.e. *account binding*, plus cloud relay.
- r/resinprinting `1t63jkj`: firmware V01.00.02.04 + *"find the QR code in the Device Info section… to proceed with the connection to the Matrix app."*
- Elegoo Privacy Agreement distinguishes **LAN mode** ("slice files transmitted directly from your computer to the printer, without passing through our cloud server") from cloud — for the Jupiter 2 the *documented* remote path is the cloud one.
- **INFERRED:** a cloud-relay-only control path is not locally replayable without reverse-engineering the app's auth, and nobody has published that.

---

## 5. The Jupiter-2-in-Home-Assistant evidence — negative and documented

This is the direct answer to the crux question.

### The one attempt in this very repo — UNANSWERED
- **`danielcherubini/elegoo-homeassistant` issue #452, "cannot connect to Jupiter 2"**
- https://github.com/danielcherubini/elegoo-homeassistant/issues/452
- Opened **2026-11-12** by `augustvitas`. **State: open. Comments: 0. Labels: none.**
- The whole failure is three log lines:
  ```
  WARNING (MainThread) [custom_components.elegoo_printer] No printer found at IP address: 192.168.50.47
  WARNING (MainThread) [custom_components.elegoo_printer] No printer found at IP address: 192.168.50.34
  ```
  reported at `config_flow.py:803` (the manual-IP WebSocket probe path).
- **Interpretation (VERIFIED from code):** `no_printer_found` here means the printer **pushed no `attributes` frame and/or no usable `MainboardID` over the SDCP WebSocket** — see the `_async_probe_websocket_printer` docstring, which refuses a printer that answers with nothing identity-bearing. So the Jupiter 2 did not speak SDCP over 3030, or refused to identify itself. **This is consistent with, and independently corroborates, the "not an SDCP board" conclusion in §4.**
- **This is the single most important citation in this whole report:** one user asked the exact question, on the exact integration, and **nobody has answered.** That absence is the evidence.

### The README's own Jupiter claim is unverified prose
- This repo's README lists **"Jupiter Range"** under "🧪 Legacy Printers (SDCP over MQTT) … supported in **Beta**."
- Introduced in **PR #316, 2026-01-31** (`b2c8714`, "Update README.md"), whose own summary was a *reword* of support guidance — not a hardware test.
- `git log -S"Jupiter" -- README.md` returns **exactly one commit**. There has never been a second, evidence-bearing Jupiter commit.
- **No Jupiter-specific code exists**: `grep -rin jupiter` across `custom_components/` returns **nothing**; the string appears only in `README.md:70`.
- **Verdict: INFERRED at best.** "Jupiter Range" in the MQTT row is a *protocol-family* inference (Jupiter = SDCP V1 like Saturn 2/3), not a device-tested claim. It is defensible for the original Jupiter and **wrong for the Jupiter 2**, which is what #452 is.

### Everything else searched came back empty
- HA Community forum: `search.json?q=elegoo` returns **exactly one topic** — "Elegoo Saturn 3 ultra" (`community.home-assistant.io/t/702470`, 2024-03, 2 posts: one user asking, one reply asking for pause/stop). **No Jupiter topic at all.**
- That Saturn thread is itself the clean illustration of the arc: Mar 2024 *"I have not been able to find any integration for it… I found this repo that seems relevant"* → the answer is now "yes, use danielcherubini's". The question was asked and answered **for Saturn, never for Jupiter.**
- GitHub global issue search for `elegoo jupiter home assistant` → **5 results total**, of which only #452 concerns connecting a Jupiter.
- HACS/default: only one Elegoo printer integration (`danielcherubini/elegoo-homeassistant`). No Jupiter-specific HACS repo exists.
- No blueprint, no Lovelace card, no add-on for Jupiter specifically.

---

## 6. Where the answer to "monitor my Jupiter in HA" actually was given

Two community threads where the question is asked and the answer is "no, or not natively":

1. **r/ElegooSaturn `1benhpi` "Home assistant integration?"** — answer: *"afaik there is no public api"*, pointing at `arsi-apli/UltraRemote`.
2. **r/ElegooJupiter** (subreddit) — recurring "can I send/monitor this" threads (`10lnw9v` "Network connection", `zp44ad` "How do i set up network printing?", `uzwzm5` "Anyone Know How To Send Sliced Files Via Network?"). Answers are all Chitubox-network-send / USB-stick, **no HA, no API**. On the original Jupiter the network send is confirmed to work but to be *"painfully slow"* (r/ElegooJupiter `10lnw9v`; Chitubox support even sent a video disproving it, so it's a Jupiter-specific throughput problem, not a capability problem).
3. **r/elegoo `1j8bi4v` "Can the Centauri Carbon connect to Home Assistant?"** — the *FDM* version of the same question. This is the pattern: **the FDM line got a working HA integration; the resin line got nothing for Jupiter 2.**

---

## 7. Search surface (so the negative is auditable)

Run 2026-11-19. If you want to re-verify "nobody has done this," these are the exact places that were checked and returned nothing.

**GitHub API (`/search/repositories`, `/search/issues`)**
- `jupiter elegoo in:name,description,readme` → 32 repos; **none** is a Jupiter network client
- `elegoo jupiter home assistant` → **0**
- `elegoo resin sdcp` → 2 (cthulhu, alfiedennen — neither Jupiter)
- `elegoo jupiter firmware` → **0**
- `jupiter2 OR "jupiter-2"` → 118, all unrelated (ROS, Jupyter, Jupiter Ace, usernames)
- issues `"Jupiter 2" elegoo in:title` → **1** (UVtools #1114)
- issues `elegoo jupiter sdcp in:title` → **0**
- issues `elegoo jupiter home assistant` → 5, only #452 relevant
- issues `"Jupiter 2" mqtt` / `"Jupiter 2" sdcp` / `"Jupiter 2" websocket 3030` → nothing relevant
- `org:Open-Resin-Alliance jupiter` → 8, all **file-format/slicer**, none network
- `repo:PtolemyDL/ResinPilot jupiter` → **0**
- `"jupiter se" elegoo sdcp` → **0**

**Repo-level inspection (not just search)**
- `dnviti`, `printlog-bridge` (#102, #97, PR #119) — **explicitly excludes resin/Jupiter**: *"Elegoo's resin printers (Saturn, Mars, Jupiter) answer M99999 too, but they print through the resin states (2, 3 and 4…) so this adapter would never see a job start."*
- `jeff-roche/farm3d` #26 — catalog of Elegoo model→protocol; **no Jupiter entry at all**, resin out of scope.
- `catduckgnaf/print-buddy` #243, `norjms/openprinthq-engine` PR #35, `breadslicer-app` #630/#633, `LM1LC3N7/gladys-elegoo`, `skribascode/elegoo-mcp-server`, `Max-Pare/open-lattice`, `runnane/elegoo-web` — **all SDCP-V3-only or CC2-only; none mention Jupiter 2.**
- `Open-Resin-Alliance/df-plugin-elegoo` **issue #18 "Jupiter 2 upload"** (opened 2026-10-20 by `glococo`) — *"I was wondering if someone already made the Manager for Jupiter 2: upload, delete, list jobs…"* — **0 comments, still open.** A second independent person asking, and a second silence.
- `OpenCentauri/*` (576★ org, firmware RE for Centauri Carbon) — **FDM only**; nothing for Jupiter.
- `elegooofficial/*` — only `Jupiter` (= original 6K, 2023) and `GOO`; **no Jupiter 2 repo, no resin SDK**. `elegoo-link` is FDM-only (§2).
- `pollstar/ha-sdcp` — inspected `printers/elegoo.py`; **no Jupiter model registered**.

**Web**
- `web_search` on: `"Elegoo Jupiter home assistant"`, `"Elegoo Jupiter mqtt"`, `"Elegoo Jupiter octoprint"`, `"Jupiter 2" mqtt/sdcp/protocol/websocket`, `"chitu board reverse engineer"`, `"Elegoo resin printer api"`, `"Jupiter 2" wireshark/port scan/teardown`, `site:community.home-assistant.io` — **no working Jupiter 2 client found.**
- HA Community Discourse search API (`/search.json?q=elegoo|elegoo saturn|elegoo mars|elegoo jupiter|elegoo resin`) → 1 topic.
- Reddit: r/ElegooJupiter, r/elegoo, r/Chitubox, r/resinprinting, r/ElegooSaturn, r/ElegooMars — searched for HA/protocol/monitor phrasing.

**Not searchable / unknown, stated honestly**
- **Discord** (Elegoo official, Chitubox, "3D Printing" servers) — not indexed, not accessible to me. A capture could exist there.
- **Private Facebook groups, Elegoo's own forums** — not checked.
- Non-English forums (German/Russian/Chinese — notably Chinese, where Chitu/Elegoo reverse engineering is most active) — **not covered.** This is the largest genuine gap.
- Whether a Jupiter 2 unit has been simply **nmap'd by anyone who then said nothing** — unknowable.

---

## 8. VERIFIED vs INFERRED (summary table)

| # | Statement | Level | Source |
|---|---|---|---|
| 1 | Multiple Elegoo **resin** printers have been integrated over the LAN by third parties since 2023 | **VERIFIED** | cassini, UltraRemote, ChitUI, mariner, cthulhu, ResinPilot, RPP, this repo |
| 2 | Original Jupiter uses **SDCP V1 over MQTT** (client-hosted broker, `M66666`) | **VERIFIED** | ResinPilot README "Found, not supported yet" lists Jupiter/Jupiter SE under SDCP 1; cassini protocol |
| 3 | Original Jupiter network-send works but is abnormally slow | **VERIFIED** | r/ElegooJupiter `10lnw9v`, incl. Chitubox support video |
| 4 | **No working Jupiter 2 network client exists anywhere public** | **VERIFIED** (absence, §7) | all of §7 |
| 5 | Jupiter 2 does not speak SDCP-WebSocket well enough to identify itself | **VERIFIED** | this repo #452 log; `no_printer_found` path |
| 6 | Jupiter 2 = Allwinner R528, OpenWrt/Tina Linux, FPGA bitstream partition | **VERIFIED** (primary user, firmware image opened) | `glococo`, UVtools #1114, 2026-09-07 |
| 7 | Jupiter 2 .goo is **LE**, GOO v5.1, new pre-layer table, new layer table, RLE, trailing checksum | **VERIFIED** (UVtools author's own decode) | `sn4k3`, UVtools #1114 |
| 8 | Jupiter 2 rejects `.ctb` entirely; SatelLite-only as tested | **VERIFIED** | Elegoo wiki FAQ + 4 independent reviews |
| 9 | Elegoo's own LAN SDK (`elegoo-link`) has **zero resin support** | **VERIFIED** | `include/types/printer.h` `PrinterType`; README model list |
| 10 | OctoPrint-Chituboard is **UART/serial**, not network — and never listed Jupiter | **VERIFIED** | repo README |
| 11 | Nobody has ever put Klipper/Moonraker on an Elegoo resin printer | **VERIFIED** | no such repo; `elegoo-link` enum is FDM-only |
| 12 | This repo's "Jupiter Range" MQTT support is **prose, not tested code** | **VERIFIED** | one commit (PR #316), zero `jupiter` in `custom_components/` |
| 13 | Jupiter 2's LAN stack resembles CC2 (printer-run MQTT + access code) rather than SDCP | **INFERRED** — circumstantial: shared R528 SoC family, no LAN-only mode documented, Matrix QR/cloud pairing. **No capture exists.** | §4 |
| 14 | Jupiter 2's cloud path cannot be replayed locally without app auth RE | **INFERRED** | Matrix QR/account binding + Elegoo privacy policy |
| 15 | Jupiter 2 checksum/signature blocks hand-built files | **INFERRED** (sn4k3's hypothesis on glococo's failing files) | UVtools #1114 |
| 16 | No Jupiter 2 capture exists in Discord/Chinese forums | **UNKNOWN — not searched** | §7 caveat |

---

## 9. What this means for the decision (not a recommendation — just the map)

**The premise "no Elegoo resin printer has ever been integrated" is false, and it would be an error to plan around it.** Resin network integration is a solved, well-documented, multiply-reinvented problem — *for SDCP machines*.

**The Jupiter 2 is not an SDCP machine**, or at least not one that behaves like the Saturn/Mars family. The evidence for that is strong and independent:
- file format diverges at the byte level (LE, v5.1, new tables, checksum),
- SoC/OS diverges (Allwinner R528 + Tina/OpenWrt + FPGA, vs Chitu MCU),
- Elegoo's own LAN SDK covers FDM only,
- the only documented connection attempt produces silence on UDP 3000 / TCP 3030,
- and Elegoo ships it as a SatelLite+Matrix (cloud) product with no LAN-only mode.

So the two realistic branches are:
1. **Capture-first.** Nobody has published a Jupiter 2 pcap. Before any design work, someone with the physical unit needs to (a) nmap it, (b) replay SatelLite's "Network Sending" and capture it, (c) test `M99999`→3000 and `ws://:3030`, (d) test CC2-style `:1883` + `{"id":0,"method":7000}`→`52700` with an access code. Any of those four outcomes would settle the architecture in an afternoon and would be **new information to the entire community** — `glococo` and `df-plugin-elegoo#18` are both explicitly asking for exactly this.
2. **Protocol-family fallback.** If the capture shows SDCP V1, the existing MQTT transport (this repo's `mqtt/`, PR #261, derived from cassini) already implements ~all of it; Jupiter 2 becomes a profile, not a project. If it shows CC2-style MQTT, the `cc2/` transport is the nearer starting point.

**The honest risk to flag upward:** the Jupiter 2's pain is not a Home Assistant gap. UVtools can't even *parse* its files and `glococo` has given up on per-layer features until Elegoo ships firmware. If Elegoo's own ecosystem is this closed on this model, a monitoring integration may be blocked by something no amount of protocol work can fix — and that is worth knowing *before* it is designed around.
