"""Simulated UPnP/DLNA media renderer that announces itself on the LAN via SSDP.

Home Assistant's SSDP discovery picks it up, fetches description.xml, and the
dlna_dmr integration opens a "discovered, not configured" flow for it - the
same path a newly plugged-in smart TV takes. Edit NAME/MANUFACTURER/MODEL below.
"""
import socket
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = "Living Room Media Player"
MANUFACTURER = "Generic"
MODEL = "MR-100"
HTTP_PORT = 18080
UDN = "uuid:" + str(uuid.uuid5(uuid.NAMESPACE_DNS, NAME))
SSDP_ADDR, SSDP_PORT = "239.255.255.250", 1900
DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"
NTS = ["upnp:rootdevice", UDN, DEVICE_TYPE]


def own_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outbound interface
    ip = s.getsockname()[0]
    s.close()
    return ip


IP = own_ip()
LOCATION = f"http://{IP}:{HTTP_PORT}/description.xml"

SERVICES = "".join(
    f"<service><serviceType>urn:schemas-upnp-org:service:{s}:1</serviceType>"
    f"<serviceId>urn:upnp-org:serviceId:{s}</serviceId>"
    f"<SCPDURL>/{s}.xml</SCPDURL><controlURL>/{s}/control</controlURL>"
    f"<eventSubURL>/{s}/event</eventSubURL></service>"
    for s in ("AVTransport", "ConnectionManager", "RenderingControl")
)
DESCRIPTION = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
<specVersion><major>1</major><minor>0</minor></specVersion>
<device>
<deviceType>{DEVICE_TYPE}</deviceType>
<friendlyName>{NAME}</friendlyName>
<manufacturer>{MANUFACTURER}</manufacturer>
<modelName>{MODEL}</modelName>
<UDN>{UDN}</UDN>
<serviceList>{SERVICES}</serviceList>
</device>
</root>""".encode()


class Http(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/description.xml":
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(DESCRIPTION)))
            self.end_headers()
            self.wfile.write(DESCRIPTION)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print("http:", self.client_address[0], fmt % args, flush=True)


def notify(nts):
    """Multicast one NOTIFY per NT with the given NTS (ssdp:alive / ssdp:byebye)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(IP))
    for nt in NTS:
        usn = UDN if nt == UDN else f"{UDN}::{nt}"
        alive = (
            "CACHE-CONTROL: max-age=1800\r\n"
            f"LOCATION: {LOCATION}\r\n"
            "SERVER: Linux/5 UPnP/1.0 MockRenderer/1.0\r\n"
        ) if nts == "ssdp:alive" else ""
        msg = (
            "NOTIFY * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
            f"{alive}NT: {nt}\r\nNTS: {nts}\r\nUSN: {usn}\r\n\r\n"
        )
        s.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))
    s.close()


def notify_loop():
    while True:
        notify("ssdp:alive")
        time.sleep(20)


def leave(*_):
    # Tell the network we're gone, so the next start counts as a new arrival.
    notify("ssdp:byebye")
    print("sent ssdp:byebye", flush=True)
    raise SystemExit(0)


def search_responder():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("", SSDP_PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(IP)))
    while True:
        data, addr = s.recvfrom(4096)
        text = data.decode(errors="replace")
        if not text.startswith("M-SEARCH"):
            continue
        st = next((l.split(":", 1)[1].strip() for l in text.split("\r\n")
                   if l.upper().startswith("ST:")), "")
        for nt in NTS:
            if st in ("ssdp:all", nt):
                usn = UDN if nt == UDN else f"{UDN}::{nt}"
                resp = (
                    "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\n"
                    f"LOCATION: {LOCATION}\r\n"
                    "SERVER: Linux/5 UPnP/1.0 MockRenderer/1.0\r\n"
                    f"ST: {nt}\r\nUSN: {usn}\r\n\r\n"
                )
                s.sendto(resp.encode(), addr)
        print("m-search from", addr[0], "st:", st, flush=True)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["byebye"]:
        notify("ssdp:byebye")
        raise SystemExit(f"sent ssdp:byebye for {UDN}")
    import signal
    signal.signal(signal.SIGTERM, leave)
    print(f"mock DLNA renderer {NAME!r} at {LOCATION}", flush=True)
    threading.Thread(target=notify_loop, daemon=True).start()
    threading.Thread(target=search_responder, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Http).serve_forever()
