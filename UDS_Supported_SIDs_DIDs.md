
# UDS REPL – Supported Services (SIDs) & Data Identifiers (DIDs)

This guide documents the **UDS services** and **data identifiers** implemented in the interactive REPL and ECU stub you’re using.  
It’s tailored to the **single‑frame ISO‑TP** demo (payload ≤ 7 bytes), so larger payloads (like full VIN) are intentionally **not** supported yet.

---

## Transport & IDs

- **Transport:** ISO‑TP **Single Frame (SF)** only → request/response payload must be **≤ 7 bytes** (the PCI byte uses the first data byte).
- **Default CAN IDs:**  
  - **Request (tester → ECU):** `0x7E0`  
  - **Response (ECU → tester):** `0x7E8`  
  You can change them in the REPL via `ids 7E0 7E8` etc.
- **Timeout:** default ~**1.8 s**, configurable via `timeout <ms>`.
- **Suppress Positive Response:** Bit7 of a **sub‑function** (e.g., `0x80`) is honored on selected services (see below).

---

## Notation

- **SID** = Service Identifier (first byte of UDS payload).  
- **DID** = Data Identifier (two bytes after 0x22).  
- **NRC** = Negative Response Code (`0x7F <reqSID> <NRC>`).
- **SPR** = Suppress Positive Response (bit7 set on sub‑function byte).

---

## Summary Table (Quick View)

| SID | Service | Sub‑functions | Positive Response | SPR (bit7) | Notes |
|-----|---------|---------------|-------------------|------------|-------|
| `0x10` | DiagnosticSessionControl | `0x01` Default, `0x03` Extended | `0x50 <sub>` | *Accepted, but replies still sent for visibility* | Extended sends interim `0x7F 10 78` first |
| `0x11` | ECUReset | `0x01` Hard, `0x03` Soft | `0x51 <sub>` | *Accepted, but replies still sent for visibility* | |
| `0x22` | ReadDataByIdentifier | DIDs listed below | `0x62 <DID> <data>` | N/A | Only short values (≤4B) |
| `0x27` | SecurityAccess | `0x01` RequestSeed, `0x02` SendKey | `0x67 01 <seed>` or `0x67 02` | N/A | Demo rule: **key = seed + 1 (16‑bit)** |
| `0x19` | ReadDTCInformation | `0x02` ReportDTCByStatusMask | `0x59 02 <DTC3> <status>` | N/A | Always returns dummy DTC `0x123456` |
| `0x31` | RoutineControl | `0x01` StartRoutine; RID `0xFF00` | `0x71 01 FF 00 00` | N/A | Unknown RIDs → NRC `0x31` |
| `0x3E` | TesterPresent | `sub` (usually `0x00`) | `0x7E <sub>` | **Yes** | If SPR set, **no** positive reply |
| `0x85` | ControlDTCSetting | `0x01` On, `0x02` Off | `0xC5 <sub>` | **Yes** | If SPR set, **no** positive reply |

**Common NRCs used:**  
- `0x11` Incorrect message length or format  
- `0x12` Sub‑function not supported  
- `0x31` Request out of range  
- `0x35` Invalid key (SecurityAccess)  
- `0x78` Response pending (interim)

---

## Detailed Service Behavior

### 0x10 – DiagnosticSessionControl
**Requests**
- `10 01` → Default session
- `10 03` → Extended session (demo simulates processing delay)

**Positive Response**
- `50 <sub>`

**Interim/Timer Behavior**
- For `10 03`, ECU first sends: `7F 10 78` *(Response Pending)*, then after ~150 ms: `50 03`.

**Errors**
- Missing sub‑function → `7F 10 11`
- Unsupported sub‑function (e.g., `10 02`) → `7F 10 12`

**SPR**
- Sub‑function may include bit7 = suppress (e.g., `10 83`). In this demo, replies are still sent to stay visible in learning mode.

---

### 0x11 – ECUReset
**Requests**
- `11 01` (hard reset), `11 03` (soft reset)

**Positive Response**
- `51 <sub>`

**Errors**
- Missing sub‑function → `7F 11 11`
- Unsupported sub‑function → `7F 11 12`

**SPR**
- If sub has bit7 set, demo still replies (learning mode).

---

### 0x22 – ReadDataByIdentifier
**Request Format**
- `22 <DID_HI> <DID_LO>`

**Positive Response**
- `62 <DID_HI> <DID_LO> <data...>`

**Supported DIDs (single‑frame values ≤ 4B)**
| DID | Meaning (demo) | Value (ASCII) | Bytes (hex) |
|-----|----------------|---------------|-------------|
| `F187` | SW Version | `"V10"` | `56 31 30` |
| `F188` | ECU Name | `"ECU"` | `45 43 55` |
| `F18C` | Boot Version | `"BT1"` | `42 54 31` |
| `F189` | HW Version | `"HW1"` | `48 57 31` |
| `F195` | Supplier Code | `"SUP"` | `53 55 50` |
| `F1A0` | Example extra | `"X1"` | `58 31` |

**Errors**
- Missing a DID byte → `7F 22 11`
- Unknown DID or too‑long value → `7F 22 31`  
  *(e.g., VIN `F190` is **not** supported in single‑frame demo)*

---

### 0x27 – SecurityAccess (demo 2‑byte seed/key)
**Requests**
- `27 01` → Request Seed → ECU returns `67 01 <seed_hi> <seed_lo>`  
- `27 02 <key_hi> <key_lo>` → Send Key

**Key Rule (demo)**
- **key = (seed + 1) & 0xFFFF**

**Positive Response**
- Seed: `67 01 <seed_hi> <seed_lo>`  
- Key accepted: `67 02`

**Errors**
- Missing sub/bytes → `7F 27 11`  
- Unsupported sub → `7F 27 12`  
- Wrong key → `7F 27 35`

---

### 0x19 – ReadDTCInformation
**Requests**
- `19 02 <statusMask>` → Report DTC by Status Mask

**Positive Response (demo)**
- Always: `59 02 12 34 56 00` → DTC `0x123456`, status `0x00`

**Errors**
- Missing sub → `7F 19 11`
- Other subs not implemented → `7F 19 12`

---

### 0x31 – RoutineControl
**Requests**
- `31 01 <RID_HI> <RID_LO>` → StartRoutine

**Supported Routine**
- `RID = 0xFF00`

**Positive Response**
- `71 01 FF 00 00` → routine started, status `0x00`

**Errors**
- Missing bytes → `7F 31 11`
- Unknown RID → `7F 31 31`
- Unsupported sub (e.g., Stop/Results) → `7F 31 12`

---

### 0x3E – TesterPresent
**Requests**
- `3E <sub>` (usually `00`)

**Positive Response**
- `7E <sub>`

**SPR**
- If sub has bit7 set (e.g., `80`), **no positive response** is sent.

---

### 0x85 – ControlDTCSetting
**Requests**
- `85 01` (ON), `85 02` (OFF)

**Positive Response**
- `C5 <sub>`

**Errors**
- Missing sub → `7F 85 11`
- Unsupported sub → `7F 85 12`

**SPR**
- If sub has bit7 set (e.g., `0x81` or `0x82`), **no positive response** is sent.

---

## Example REPL Commands

```text
# Sessions & Reset
dsc 03
reset 01

# DIDs
rdid F187
rdid F188
rdid F18C
rdid F189
rdid F195
rdid F1A0

# Security Access flow
sa seed             # read seed (e.g., 67 01 12 AB)
sa key 12 AC        # send seed+1 (here 0x12AB + 1)

# DTC report
dtc mask FF

# Routine
rc start FF00

# Tester Present & Control DTC
send 3E 00
send 85 02

# Suppress positive (no reply by design)
send 3E 80
send 85 81
```

---

## Limitations & Next Steps

- **Single‑Frame only:** any payload beyond 7 bytes is not supported (e.g., VIN `F190`).  
- **SecurityAccess:** demo logic for learning only; not representative of real ECUs.  
- **ReadDTCInformation:** simplified; always returns one dummy DTC.

**Recommended upgrade:** move to **ISO‑TP multi‑frame** (using `isotp` and optionally `udsoncan`) to support long DIDs (VIN), file transfer (`0x34/0x36`), and realistic flow control (`FF/CF/FC`).

---

*Version:* This document describes the behavior of the `uds_repl.py` stub you generated with ChatGPT today.
