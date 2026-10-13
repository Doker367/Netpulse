"""Listener Telnet señuelo.

Simula un login con banner por vendor y, tras "autenticar" (siempre
acepta, como los honeypots reales, para capturar la sesión), ofrece una
shell falsa que registra cada comando del atacante.
"""

from app.services.deception.listeners.base import BaseListener, read_line
from app.services.deception import personas


class TelnetListener(BaseListener):
    proto = "telnet"
    MAX_COMMANDS = 50

    async def handle(self, reader, writer, src_ip, src_port):
        banner = personas.banner_for(self.role, self.banner)
        await self.write(writer, f"\r\n{banner}\r\n".encode())

        username = await self._prompt(reader, writer, "login: ")
        if username is None:
            return
        await self.emit(src_ip, "login_attempt", username=username, src_port=src_port,
                        detail={"service": "telnet"})

        password = await self._prompt(reader, writer, "Password: ")
        if password is None:
            return

        creds = personas.credentials_for(self.role)
        success = (username, password) in creds
        await self.emit(
            src_ip,
            "login_success" if success else "login_attempt",
            username=username,
            success=success,
            src_port=src_port,
            detail={"service": "telnet", "password": password},
        )

        # Aun con credenciales no débiles, abrimos shell para capturar
        # comandos (comportamiento de decepción).
        prompt = personas.prompt_for(self.role)
        await self.write(writer, f"\r\n{prompt}".encode())
        for _ in range(self.MAX_COMMANDS):
            command = await read_line(reader)
            if command is None:
                break
            if not command:
                await self.write(writer, prompt.encode())
                continue
            await self.emit(src_ip, "command", username=username,
                            detail={"service": "telnet", "command": command})
            response = personas.shell_response(self.role, self.vendor, command)
            out = f"{response}\r\n{prompt}" if response else prompt
            await self.write(writer, out.encode())

    async def _prompt(self, reader, writer, text):
        await self.write(writer, text.encode())
        value = await read_line(reader)
        return value
