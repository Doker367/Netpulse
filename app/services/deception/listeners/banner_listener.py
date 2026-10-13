"""Listener señuelo para protocolos "banner" (RTSP, SMB...).

RTSP se responde de forma **protocolo-correcta** a OPTIONS/DESCRIBE/SETUP/
PLAY/TEARDOWN con un SDP que describe una pista H.264, de modo que VLC,
ONVIF/RTSP scanners y nmap ven una cámara IP real. El servidor de video
(RTP real) no se emula aquí; para video real usar un media server externo.

SMB u otros solo registran el intento (suficiente para saber que el
atacante tocó el dispositivo).
"""

import asyncio

from app.services.deception.listeners.base import BaseListener

_SERVER_BY_VENDOR = {
    "hikvision": "Hikvision-Webs",
    "dahua": "Dahua Rtsp Server",
    "axis": "Axis RTSP/1.0",
}


def _sdp(host: str, title: str, session_id: int) -> bytes:
    body = (
        "v=0\r\n"
        f"o=- {session_id} 1 IN IP4 {host}\r\n"
        f"s={title}\r\n"
        f"c=IN IP4 {host}\r\n"
        "t=0 0\r\n"
        "m=video 0 RTP/AVP 96\r\n"
        "a=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1\r\n"
        "a=control:trackID=1\r\n"
    )
    return body.encode("latin-1", "replace")


class BannerListener(BaseListener):
    proto = "rtsp"
    READ_WAIT = 5.0
    _session_seq = 1000000

    async def handle(self, reader, writer, src_ip, src_port):
        event_type = "rtsp_request" if self.proto == "rtsp" else "other"
        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=self.READ_WAIT)
        except asyncio.TimeoutError:
            data = b""

        request = data.decode("latin-1", errors="replace")
        method = request.split(" ", 1)[0].upper().strip() if request else ""
        cseq = _header(request, "CSeq") or "1"
        await self.emit(
            src_ip, event_type, src_port=src_port,
            detail={"service": self.proto, "request": request[:200], "method": method},
        )

        if self.proto != "rtsp" or not method:
            return

        server = _SERVER_BY_VENDOR.get((self.vendor or "").lower(), "DSS/6.0")
        cseq_h = f"CSeq: {cseq}\r\nServer: {server}\r\n"

        if method == "OPTIONS":
            await self.write(writer, (
                f"RTSP/1.0 200 OK\r\n{cseq_h}"
                "Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN, GET_PARAMETER\r\n\r\n"
            ).encode())
        elif method == "DESCRIBE":
            BannerListener._session_seq += 1
            sdp = _sdp(self.host, f"{self.device_id} stream", BannerListener._session_seq)
            await self.write(writer, (
                f"RTSP/1.0 200 OK\r\n{cseq_h}"
                f"Content-Base: rtsp://{self.host}/Streaming/Channels/101/\r\n"
                "Content-Type: application/sdp\r\n"
                f"Content-Length: {len(sdp)}\r\n\r\n"
            ).encode() + sdp)
        elif method == "SETUP":
            transport = _header(request, "Transport") or "RTP/AVP/TCP;unicast;interleaved=0-1"
            await self.write(writer, (
                f"RTSP/1.0 200 OK\r\n{cseq_h}"
                f"Transport: {transport}\r\n"
                "Session: 12345678;timeout=60\r\n\r\n"
            ).encode())
        elif method == "PLAY":
            await self.write(writer, (
                f"RTSP/1.0 200 OK\r\n{cseq_h}"
                "Session: 12345678\r\n"
                "Range: npt=0.000-\r\n"
                "RTP-Info: url=rtsp://" + self.host + "/Streaming/Channels/101/trackID=1;seq=1;rtptime=0\r\n\r\n"
            ).encode())
        elif method in ("TEARDOWN", "GET_PARAMETER"):
            await self.write(writer, f"RTSP/1.0 200 OK\r\n{cseq_h}\r\n".encode())
        else:
            await self.write(writer, (
                f"RTSP/1.0 501 Not Implemented\r\n{cseq_h}\r\n"
            ).encode())


def _header(request: str, name: str):
    for line in request.split("\r\n"):
        if line.lower().startswith(name.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return None
