import socket

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

WEB_PORT = 8000
REDIRECT_PORT = 8001


def lan_address():
    """This machine's address on its local network.

    Opening a UDP socket towards a routable address makes the OS choose the
    outbound interface and fills in the local address. Nothing is ever sent.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


class Command(BaseCommand):
    help = "Show the addresses needed to reach the development servers from another device on the network."

    def handle(self, *args, **options):
        address = lan_address()
        if address is None:
            raise CommandError("No network address found. Is this machine connected to a network?")

        web = f"http://{address}:{WEB_PORT}"
        redirect = f"http://{address}:{REDIRECT_PORT}"

        self.stdout.write(self.style.MIGRATE_HEADING("LAN address"))
        self.stdout.write(f"  {address}\n\n")

        self.stdout.write(self.style.MIGRATE_HEADING("backend/.env"))
        self.stdout.write(f"  WEB_DOMAIN={web}")
        self.stdout.write(f"  SHORT_DOMAIN={redirect}\n\n")

        stale = []
        if settings.WEB_DOMAIN != web:
            stale.append(f"WEB_DOMAIN is {settings.WEB_DOMAIN}")
        if settings.SHORT_DOMAIN.rstrip("/") != redirect:
            stale.append(f"SHORT_DOMAIN is {settings.SHORT_DOMAIN}")

        if stale:
            self.stdout.write(self.style.WARNING("Current settings do not match this address:"))
            for line in stale:
                self.stdout.write(self.style.WARNING(f"  {line}"))
            self.stdout.write(
                self.style.WARNING("  Short links and QR codes will point somewhere other devices cannot reach.\n")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Settings match this address.\n"))

        self.stdout.write(self.style.MIGRATE_HEADING("Run the servers on every interface"))
        self.stdout.write(f"  DJANGO_ROOT_URLCONF=config.urls_web      manage.py runserver 0.0.0.0:{WEB_PORT}")
        self.stdout.write(f"  DJANGO_ROOT_URLCONF=config.urls_redirect manage.py runserver 0.0.0.0:{REDIRECT_PORT}\n\n")

        self.stdout.write(self.style.MIGRATE_HEADING("Open on another device"))
        self.stdout.write(f"  {web}\n")
