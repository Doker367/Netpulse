"""Listener FTP señuelo.

Simula un servidor FTP: banner, login (USER/PASS) y un conjunto mínimo de
comandos. Registra credenciales e intentos para la telemetría.
"""

from app.services.deception import personas
from app.services.deception.listeners.base import BaseListener, read_line


class FtpListener(BaseListener):
    proto = "ftp"

    async def handle(self, reader, writer, src_ip, src_port):
        banner = personas.banner_for(self.role, self.banner)
        await self.write(writer, f"220 {banner} FTP server ready.\r\n".encode())

        username = "anonymous"
        for _ in range(30):
            line = await read_line(reader)
            if line is None:
                return
            command, _, arg = line.partition(" ")
            command = command.upper().strip()

            if command == "USER":
                username = arg.strip() or "anonymous"
                await self.emit(src_ip, "login_attempt", username=username,
                                src_port=src_port, detail={"service": "ftp"})
                await self.write(writer, b"331 Please specify the password.\r\n")
            elif command == "PASS":
                await self.emit(
                    src_ip, "login_success", username=username, success=True,
                    src_port=src_port,
                    detail={"service": "ftp", "password": arg.strip()},
                )
                await self.write(writer, b"230 Login successful.\r\n")
            elif command == "PWD":
                await self.write(writer, b'257 "/" is the current directory.\r\n')
            elif command in ("LIST", "NLST"):
                await self.write(writer, b"150 Here comes the directory listing.\r\n")
                await self.write(writer, b"-rw-r--r-- 1 0 0 1024 Jan 01 00:00 backup.tar.gz\r\n")
                await self.write(writer, b"226 Directory send OK.\r\n")
            elif command == "SYST":
                await self.write(writer, b"215 UNIX Type: L8\r\n")
            elif command == "QUIT":
                await self.write(writer, b"221 Goodbye.\r\n")
                return
            elif command == "FEAT":
                await self.write(writer, b"211-Features:\r\n PASV\r\n211 End\r\n")
            else:
                await self.write(writer, b"500 Unknown command.\r\n")
