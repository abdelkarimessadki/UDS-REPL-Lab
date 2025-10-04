import threading
import time
import random
import can
from dataclasses import dataclass

# ================= ISO-TP Single-Frame utilities =================
def make_isotp_single_frame(payload: bytes) -> bytes:
    if len(payload) > 7:
        raise ValueError("Single Frame payload must be <= 7 bytes (got %d)" % len(payload))
    pci = len(payload) & 0x0F  # high nibble=0 for SF
    data = bytes([pci]) + payload
    return data.ljust(8, b"\x00")

def parse_isotp_single_frame(data: bytes) -> bytes:
    if not data:
        return b""
    length = data[0] & 0x0F
    return data[1:1+length]

def hexstr(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)

def parse_hex_line(line: str) -> bytes:
    parts = line.replace(",", " ").split()
    vals = []
    for p in parts:
        vals.append(int(p, 16))
    return bytes(vals)

# ================= Minimal ECU stub (server) =================
@dataclass
class ECUConfig:
    req_id: int = 0x7E0  # Tester -> ECU
    res_id: int = 0x7E8  # ECU -> Tester

class MiniECUStub(threading.Thread):
    """
    UDS ECU emulator (Single-Frame only). Supported:
      - 0x10 01/03 (03 sends 0x7F 10 78 then 0x50 03)
      - 0x11 01/03 -> 0x51 sub
      - 0x22 F1xx map below
      - 0x27 01 -> seed (2 bytes); 0x27 02 <key> -> OK if key == (seed+1) & 0xFFFF
      - 0x19 02 <mask> -> one dummy DTC
      - 0x31 01 <rid_hi> <rid_lo> (FF00) -> 0x71 01 FF 00 00
      - 0x3E sub (supports suppress positive bit7)
      - 0x85 sub (supports suppress positive bit7)
    Negative defaults:
      - 0x11 = incorrect length
      - 0x12 = sub-function not supported
      - 0x31 = request out of range
      - 0x35 = invalid key
      - 0x78 = response pending
    """
    def __init__(self, bus: can.BusABC, cfg: ECUConfig):
        super().__init__(daemon=True)
        self.bus = bus
        self.cfg = cfg
        self._running = True
        self.unlocked = False
        self._last_seed = None

        # Keep values short (<=4 bytes) so we remain single-frame
        self.did_map = {
            (0xF1, 0x87): b"V10",   # SW version
            (0xF1, 0x88): b"ECU",   # ECU name
            (0xF1, 0x8C): b"BT1",   # Boot version
            (0xF1, 0x89): b"HW1",   # HW version
            (0xF1, 0x95): b"SUP",   # Supplier code
            (0xF1, 0xA0): b"X1",    # Example extra DID
        }

    def stop(self):
        self._running = False

    def send_sf(self, arb_id: int, payload: bytes):
        msg = can.Message(arbitration_id=arb_id, is_extended_id=False,
                          data=make_isotp_single_frame(payload))
        self.bus.send(msg)

    def _send_nrc(self, sid: int, nrc: int):
        self.send_sf(self.cfg.res_id, bytes([0x7F, sid, nrc]))

    def _sub_spr(self, sub: int) -> (int, bool):
        """Return (sub_wo_bit7, suppress_positive)"""
        suppress = (sub & 0x80) != 0
        return (sub & 0x7F), suppress

    def run(self):
        while self._running:
            msg = self.bus.recv(timeout=0.1)
            if msg is None or msg.arbitration_id != self.cfg.req_id or len(msg.data) == 0:
                continue

            uds = parse_isotp_single_frame(msg.data)
            if not uds:
                continue

            sid = uds[0]

            # 0x10 DiagnosticSessionControl
            if sid == 0x10:
                if len(uds) < 2:
                    self._send_nrc(0x10, 0x11)
                    continue
                sub = uds[1]
                sub_wo, suppress = self._sub_spr(sub)
                if sub_wo in (0x01, 0x03):
                    # Simulate 0x78 for Extended Session only
                    if sub_wo == 0x03:
                        # response pending
                        self._send_nrc(0x10, 0x78)
                        time.sleep(0.15)
                    # Positive
                    if not suppress:
                        self.send_sf(self.cfg.res_id, bytes([0x50, sub_wo]))
                else:
                    self._send_nrc(0x10, 0x12)

            # 0x11 ECUReset
            elif sid == 0x11:
                if len(uds) < 2:
                    self._send_nrc(0x11, 0x11)
                    continue
                sub = uds[1]
                sub_wo, suppress = self._sub_spr(sub)
                if sub_wo in (0x01, 0x03):
                    if not suppress:
                        self.send_sf(self.cfg.res_id, bytes([0x51, sub_wo]))
                else:
                    self._send_nrc(0x11, 0x12)

            # 0x22 ReadDataByIdentifier
            elif sid == 0x22:
                if len(uds) < 3:
                    self._send_nrc(0x22, 0x11)
                    continue
                did_hi, did_lo = uds[1], uds[2]
                value = self.did_map.get((did_hi, did_lo))
                if value is not None and len(value) <= 4:
                    self.send_sf(self.cfg.res_id, bytes([0x62, did_hi, did_lo]) + value)
                else:
                    self._send_nrc(0x22, 0x31)  # out of range / not supported

            # 0x27 SecurityAccess (2-byte demo)
            elif sid == 0x27:
                if len(uds) < 2:
                    self._send_nrc(0x27, 0x11)
                    continue
                sub = uds[1]
                if sub == 0x01:  # request seed
                    self._last_seed = random.randint(0, 0xFFFF)
                    seed_hi = (self._last_seed >> 8) & 0xFF
                    seed_lo = self._last_seed & 0xFF
                    self.send_sf(self.cfg.res_id, bytes([0x67, 0x01, seed_hi, seed_lo]))
                elif sub == 0x02:  # send key
                    if len(uds) < 4:
                        self._send_nrc(0x27, 0x11)
                        continue
                    key = (uds[2] << 8) | uds[3]
                    expected = ((self._last_seed or 0) + 1) & 0xFFFF
                    if key == expected:
                        self.unlocked = True
                        self.send_sf(self.cfg.res_id, bytes([0x67, 0x02]))
                    else:
                        self.unlocked = False
                        self._send_nrc(0x27, 0x35)  # invalid key
                else:
                    self._send_nrc(0x27, 0x12)

            # 0x19 ReadDTCInformation (simple demo: sub 0x02 mask)
            elif sid == 0x19:
                if len(uds) < 2:
                    self._send_nrc(0x19, 0x11)
                    continue
                sub = uds[1]
                if sub == 0x02:  # ReportDTCByStatusMask
                    mask = uds[2] if len(uds) >= 3 else 0xFF
                    # Respond with one dummy DTC 0x123456 and status 0x00 (compact demo)
                    # Positive: 0x59 02 <DTC(3B)> <status(1B)>
                    self.send_sf(self.cfg.res_id, bytes([0x59, 0x02, 0x12, 0x34, 0x56, 0x00]))
                else:
                    self._send_nrc(0x19, 0x12)

            # 0x31 RoutineControl (StartRoutine 0xFF00)
            elif sid == 0x31:
                if len(uds) < 2:
                    self._send_nrc(0x31, 0x11)
                    continue
                sub = uds[1]
                if sub == 0x01:  # StartRoutine
                    if len(uds) < 4:
                        self._send_nrc(0x31, 0x11)
                        continue
                    rid_hi, rid_lo = uds[2], uds[3]
                    rid = (rid_hi << 8) | rid_lo
                    if rid == 0xFF00:
                        # 0x71 01 <rid> <status_byte>
                        self.send_sf(self.cfg.res_id, bytes([0x71, 0x01, rid_hi, rid_lo, 0x00]))
                    else:
                        self._send_nrc(0x31, 0x31)  # request out of range (unknown routine)
                else:
                    self._send_nrc(0x31, 0x12)

            # 0x3E TesterPresent (support suppress positive)
            elif sid == 0x3E:
                sub = uds[1] if len(uds) >= 2 else 0x00
                sub_wo, suppress = self._sub_spr(sub)
                if not suppress:
                    self.send_sf(self.cfg.res_id, bytes([0x7E, sub_wo]))
                # If suppress set, do nothing (no positive response)

            # 0x85 ControlDTCSetting (support suppress positive)
            elif sid == 0x85:
                if len(uds) < 2:
                    self._send_nrc(0x85, 0x11)
                    continue
                sub = uds[1]
                sub_wo, suppress = self._sub_spr(sub)
                if sub_wo in (0x01, 0x02):  # on/off
                    if not suppress:
                        self.send_sf(self.cfg.res_id, bytes([0xC5, sub_wo]))
                else:
                    self._send_nrc(0x85, 0x12)

            else:
                # Service not supported
                self._send_nrc(sid, 0x11)

# ================= UDS client (Single-Frame) =================
class UDSClientSF:
    def __init__(self, bus: can.BusABC, req_id=0x7E0, res_id=0x7E8, timeout=1.5, show_interim=True):
        self.bus = bus
        self.req_id = req_id
        self.res_id = res_id
        self.timeout = timeout
        self.show_interim = show_interim

    def set_ids(self, req_id: int, res_id: int):
        self.req_id, self.res_id = req_id, res_id

    def send(self, payload: bytes) -> bytes:
        msg = can.Message(arbitration_id=self.req_id, is_extended_id=False,
                          data=make_isotp_single_frame(payload))
        self.bus.send(msg)
        start = time.time()
        last_interim = None
        while time.time() - start < self.timeout:
            r = self.bus.recv(timeout=0.05)
            if r and r.arbitration_id == self.res_id:
                pl = parse_isotp_single_frame(r.data)
                # If interim response (e.g., 0x7F .. 0x78), show it and continue waiting
                if len(pl) >= 3 and pl[0] == 0x7F and pl[2] == 0x78:
                    if self.show_interim and pl != last_interim:
                        print("INTERIM:", hexstr(pl), "| Response Pending")
                        last_interim = pl
                    # keep waiting for final
                    continue
                return pl
        raise TimeoutError("No response from ECU")

# ================= Pretty explainers =================
def explain_response(resp: bytes) -> str:
    if not resp:
        return "Empty response"
    sid = resp[0]
    if sid == 0x7F and len(resp) >= 3:
        nrc_map = {
            0x11: "Incorrect length or format",
            0x12: "Sub-function not supported",
            0x31: "Request out of range",
            0x35: "Invalid key",
            0x78: "Response pending",
        }
        return f"Negative(0x{resp[1]:02X}) NRC=0x{resp[2]:02X} ({nrc_map.get(resp[2],'?')})"
    if sid == 0x50 and len(resp) >= 2:
        return f"DiagnosticSessionControl OK (sub 0x{resp[1]:02X})"
    if sid == 0x51 and len(resp) >= 2:
        return f"ECUReset OK (sub 0x{resp[1]:02X})"
    if sid == 0x62 and len(resp) >= 3:
        did = (resp[1] << 8) | resp[2]
        val = resp[3:]
        try:
            return f"ReadDID 0x{did:04X} OK -> {hexstr(val)} ('{val.decode('ascii')}')"
        except UnicodeDecodeError:
            return f"ReadDID 0x{did:04X} OK -> {hexstr(val)}"
    if sid == 0x67 and len(resp) >= 2:
        sub = resp[1]
        if sub == 0x01 and len(resp) >= 4:
            seed = (resp[2] << 8) | resp[3]
            return f"SecurityAccess Seed: 0x{seed:04X}"
        if sub == 0x02:
            return "SecurityAccess Key accepted"
    if sid == 0x59 and len(resp) >= 6 and resp[1] == 0x02:
        dtc = (resp[2] << 16) | (resp[3] << 8) | resp[4]
        status = resp[5]
        return f"DTC report: 0x{dtc:06X}, status=0x{status:02X}"
    if sid == 0x71 and len(resp) >= 5 and resp[1] == 0x01:
        rid = (resp[2] << 8) | resp[3]
        status = resp[4]
        return f"Routine 0x{rid:04X} started, status=0x{status:02X}"
    if sid == 0x7E and len(resp) >= 2:
        return f"TesterPresent OK (sub 0x{resp[1]:02X})"
    if sid == 0xC5 and len(resp) >= 2:
        return f"ControlDTCSetting OK (sub 0x{resp[1]:02X})"
    return f"Positive response: {hexstr(resp)}"

# ================= REPL =================
def print_help():
    print("""
Commands:
  send <hex...>        - Raw UDS request (SF). e.g. send 10 03 ; send 22 F1 87
  dsc <01|03>          - DiagnosticSessionControl (01 default, 03 extended)
  reset <01|03>        - ECUReset hard/soft
  rdid <DID>           - ReadDataByIdentifier, e.g. rdid F187
  sa seed              - SecurityAccess: request seed (27 01)
  sa key <2B>          - SecurityAccess: send key (27 02 <key_hi> <key_lo>)
  rc start <RID>       - RoutineControl StartRoutine; e.g. rc start FF00
  dtc mask <mask>      - ReadDTCInformation report by status mask; e.g. dtc mask FF
  ids <req> <res>      - Set CAN IDs in hex; e.g. ids 7E0 7E8
  timeout <ms>         - Set response timeout in milliseconds
  show                 - Show current settings
  help                 - This help
  exit / quit          - Leave the console

ECU stub supports SIDs: 10, 11, 22, 27, 19, 31, 3E, 85 (single-frame only).
DIDs: F187='V10', F188='ECU', F18C='BT1', F189='HW1', F195='SUP', F1A0='X1'
Notes:
  • 'send' payload must be <= 7 bytes (single-frame).
  • Bit7 suppress positive respected for 3E and 85, optional for 10/11 (ignored if you want responses).
  • DSC(03) shows INTERIM 'Response Pending' before final positive.
""")

def main():
    cfg = ECUConfig()

    # two buses on same virtual channel so they see each other
    with can.Bus(bustype="virtual", channel=0) as ecu_bus, \
         can.Bus(bustype="virtual", channel=0) as client_bus:

        ecu = MiniECUStub(ecu_bus, cfg)
        ecu.start()

        client = UDSClientSF(client_bus, req_id=cfg.req_id, res_id=cfg.res_id, timeout=1.8, show_interim=True)
        time.sleep(0.05)  # let ECU start

        print("UDS REPL (Single-Frame). Type 'help' for commands.")
        print(f"Using CAN IDs: req=0x{client.req_id:03X}, res=0x{client.res_id:03X}")
        try:
            while True:
                line = input("uds> ").strip()
                if not line:
                    continue
                cmd, *rest = line.split(maxsplit=1)
                cmd = cmd.lower()

                if cmd in ("exit", "quit"):
                    break
                elif cmd == "help":
                    print_help()
                elif cmd == "show":
                    print(f"req_id=0x{client.req_id:03X}, res_id=0x{client.res_id:03X}, timeout={int(client.timeout*1000)} ms")

                elif cmd == "ids":
                    if not rest:
                        print("Usage: ids <req_hex> <res_hex>")
                        continue
                    try:
                        parts = rest[0].split()
                        if len(parts) != 2:
                            raise ValueError
                        req = int(parts[0], 16)
                        res = int(parts[1], 16)
                        client.set_ids(req, res)
                        print(f"IDs updated: req=0x{req:03X}, res=0x{res:03X}")
                    except Exception:
                        print("Invalid IDs. Example: ids 7E0 7E8")

                elif cmd == "timeout":
                    if not rest:
                        print("Usage: timeout <milliseconds>")
                        continue
                    try:
                        ms = int(rest[0])
                        client.timeout = ms / 1000.0
                        print(f"Timeout set to {ms} ms")
                    except Exception:
                        print("Invalid milliseconds")

                elif cmd == "send":
                    if not rest:
                        print("Usage: send <hex bytes>; example: send 22 F1 87")
                        continue
                    try:
                        payload = parse_hex_line(rest[0])
                        if len(payload) == 0:
                            print("No bytes parsed.")
                            continue
                        if len(payload) > 7:
                            print("Too long for Single-Frame (max 7 bytes).")
                            continue
                        resp = client.send(payload)
                        print("TX:", hexstr(payload))
                        print("RX:", hexstr(resp), "|", explain_response(resp))
                    except ValueError as ve:
                        print("Parse error:", ve)
                    except TimeoutError as te:
                        print("Timeout:", te)

                elif cmd == "dsc":
                    sub = 0x03
                    if rest:
                        try:
                            sub = int(rest[0], 16)
                        except Exception:
                            pass
                    payload = bytes([0x10, sub])
                    try:
                        resp = client.send(payload)
                        print("TX:", hexstr(payload))
                        print("RX:", hexstr(resp), "|", explain_response(resp))
                    except TimeoutError as te:
                        print("Timeout:", te)

                elif cmd == "reset":
                    sub = 0x01
                    if rest:
                        try:
                            sub = int(rest[0], 16)
                        except Exception:
                            pass
                    payload = bytes([0x11, sub])
                    try:
                        resp = client.send(payload)
                        print("TX:", hexstr(payload))
                        print("RX:", hexstr(resp), "|", explain_response(resp))
                    except TimeoutError as te:
                        print("Timeout:", te)

                elif cmd == "rdid":
                    if not rest:
                        print("Usage: rdid <DIDhex>; e.g. rdid F187")
                        continue
                    try:
                        did = int(rest[0], 16)
                        payload = bytes([0x22, (did >> 8) & 0xFF, did & 0xFF])
                        resp = client.send(payload)
                        print("TX:", hexstr(payload))
                        print("RX:", hexstr(resp), "|", explain_response(resp))
                    except Exception as e:
                        print("Error:", e)

                elif cmd == "sa":
                    if not rest:
                        print("Usage: sa seed  |  sa key <2B>")
                        continue
                    subcmd = rest[0].split()
                    if subcmd[0].lower() == "seed":
                        payload = bytes([0x27, 0x01])
                        try:
                            resp = client.send(payload)
                            print("TX:", hexstr(payload))
                            print("RX:", hexstr(resp), "|", explain_response(resp))
                        except TimeoutError as te:
                            print("Timeout:", te)
                    elif subcmd[0].lower() == "key":
                        if len(subcmd) != 2:
                            print("Usage: sa key <2B>   e.g. sa key 12 34 or sa key 1234")
                            continue
                        # Support "1234" or "12 34"
                        ktext = subcmd[1].replace(" ", "")
                        if len(ktext) == 4:
                            key = int(ktext, 16)
                            payload = bytes([0x27, 0x02, (key >> 8) & 0xFF, key & 0xFF])
                            try:
                                resp = client.send(payload)
                                print("TX:", hexstr(payload))
                                print("RX:", hexstr(resp), "|", explain_response(resp))
                            except TimeoutError as te:
                                print("Timeout:", te)
                        else:
                            # maybe two bytes space-separated?
                            try:
                                keyb = parse_hex_line(subcmd[1])
                                if len(keyb) != 2:
                                    raise ValueError
                                payload = bytes([0x27, 0x02]) + keyb
                                resp = client.send(payload)
                                print("TX:", hexstr(payload))
                                print("RX:", hexstr(resp), "|", explain_response(resp))
                            except Exception:
                                print("Provide a 2-byte key (e.g., 12 34 or 1234)")
                    else:
                        print("Usage: sa seed  |  sa key <2B>")

                elif cmd == "rc":
                    if not rest:
                        print("Usage: rc start <RID>")
                        continue
                    subcmd = rest[0].split()
                    if len(subcmd) == 2 and subcmd[0].lower() == "start":
                        try:
                            rid = int(subcmd[1], 16)
                            payload = bytes([0x31, 0x01, (rid >> 8) & 0xFF, rid & 0xFF])
                            resp = client.send(payload)
                            print("TX:", hexstr(payload))
                            print("RX:", hexstr(resp), "|", explain_response(resp))
                        except Exception as e:
                            print("Error:", e)
                    else:
                        print("Usage: rc start <RID>")

                elif cmd == "dtc":
                    if not rest:
                        print("Usage: dtc mask <hexMask>")
                        continue
                    subcmd = rest[0].split()
                    if len(subcmd) == 2 and subcmd[0].lower() == "mask":
                        try:
                            mask = int(subcmd[1], 16) & 0xFF
                            payload = bytes([0x19, 0x02, mask])
                            resp = client.send(payload)
                            print("TX:", hexstr(payload))
                            print("RX:", hexstr(resp), "|", explain_response(resp))
                        except Exception as e:
                            print("Error:", e)
                    else:
                        print("Usage: dtc mask <hexMask>")

                else:
                    print("Unknown command. Type 'help'.")

        finally:
            ecu.stop()
            ecu.join(timeout=0.2)

if __name__ == "__main__":
    main()
