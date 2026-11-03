"""Kernel firewall integration test, exclusively inside fresh network namespaces.

Run as a normal user: /usr/bin/python3 tests/firewall_namespace.py
Requires unprivileged user namespaces, nft, ip, wg and the WireGuard kernel driver.
No traffic or firewall commands can reach the original network namespace.
"""

import errno
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyberghost_desktop.firewall import Firewall, TABLE, authorized_action, request  # noqa: E402
from cyberghost_desktop.protected_connect import connection_rule, remove_exception  # noqa: E402


def run(*args, source=None):
    return subprocess.run(args, input=source, capture_output=True, text=True, check=True).stdout


def send_datagram(address, *, mark=None, existing=None):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    connection = existing or socket.socket(family, socket.SOCK_DGRAM)
    try:
        connection.settimeout(1)
        if mark is not None:
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
        connection.sendto(b"namespace test", (address, 9999))
    finally:
        if existing is None:
            connection.close()


def blocked(address, **kwargs):
    try:
        send_datagram(address, **kwargs)
    except OSError as error:
        assert error.errno in {errno.EPERM, errno.EACCES}, error
    else:
        raise AssertionError(f"Direct traffic to {address} was not blocked")


PEER = '''
import socket, socketserver, subprocess, sys, threading
from pathlib import Path
def ip(*args):
    subprocess.run(["ip", *args], check=True, stdout=subprocess.DEVNULL)
class Service(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        self.request.recv(1024)
        self.request.sendall(b"HTTP/1.1 200 OK\\r\\nContent-Length: 5\\r\\nConnection: close\\r\\n\\r\\nlocal")
class Server6(socketserver.TCPServer):
    address_family = socket.AF_INET6
print("ready", flush=True)
for command in sys.stdin:
    command = command.strip()
    if command == "setup":
        ip("link", "set", "lo", "up")
        for name in ("all", "default", "client0"):
            Path(f"/proc/sys/net/ipv6/conf/{name}/disable_ipv6").write_text("0")
        ip("addr", "add", "172.17.0.2/24", "dev", "client0")
        ip("addr", "add", "198.19.0.2/24", "dev", "client0")
        ip("-6", "addr", "add", "fd17::2/64", "dev", "client0", "nodad")
        ip("-6", "addr", "add", "2001:db8:19::2/64", "dev", "client0", "nodad")
        ip("link", "set", "client0", "up")
        ip("route", "add", "default", "via", "172.17.0.1")
        ip("-6", "route", "add", "default", "via", "fd17::1")
        for server_type, address in ((socketserver.TCPServer, '172.17.0.2'), (Server6, 'fd17::2')):
            server = server_type((address, 11434), Service)
            threading.Thread(target=server.serve_forever, daemon=True).start()
    elif command == "stop":
        break
    else:
        address, *source = command.split()
        with socket.socket(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_DGRAM) as s:
            if source:
                s.bind((source[0], 0))
            s.sendto(b"forwarding test", (address, 9999))
    print("ok", flush=True)
'''


def inner(original_namespace):
    assert os.readlink("/proc/self/ns/net") != original_namespace, "Refusing to test in the original network namespace"
    assert os.geteuid() == 0
    assert not [entry for entry in json.loads(run("nft", "-j", "list", "tables"))["nftables"] if "table" in entry]
    run("ip", "link", "set", "lo", "up")
    run("ip", "link", "add", "plain0", "type", "dummy")
    run("ip", "addr", "add", "198.18.0.1/24", "dev", "plain0")
    run("ip", "link", "set", "plain0", "up")
    for name in ("all", "default", "plain0"):
        Path(f"/proc/sys/net/ipv6/conf/{name}/disable_ipv6").write_text("0")
    run("ip", "-6", "addr", "add", "2001:db8:18::1/64", "dev", "plain0", "nodad")
    run("ip", "-6", "addr", "add", "fd18::1/64", "dev", "plain0", "nodad")
    for address in ("10.20.0.1/24", "192.168.50.1/24", "169.254.25.1/24"):
        run("ip", "addr", "add", address, "dev", "plain0")
    run("ip", "route", "add", "default", "dev", "plain0")
    run("ip", "-6", "route", "add", "default", "dev", "plain0")
    # A separate application's table must survive activation and removal.
    run("nft", "-f", "-", source='''table inet namespace_observer {
 counter before { }
 counter after { }
 chain before { type filter hook forward priority 0; policy accept; counter name before; }
 chain after { type filter hook forward priority 30; policy accept; counter name after; }
}
''')
    peer = subprocess.Popen(["unshare", "--net", "/usr/bin/python3", "-u", "-c", PEER],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert peer.stdout.readline().strip() == "ready"
    run("ip", "link", "add", "lan0", "type", "veth", "peer", "name", "client0")
    run("ip", "link", "set", "client0", "netns", str(peer.pid))
    run("ip", "addr", "add", "198.19.0.1/24", "dev", "lan0")
    run("ip", "addr", "add", "172.17.0.1/24", "dev", "lan0")
    run("ip", "-6", "addr", "add", "fd17::1/64", "dev", "lan0", "nodad")
    run("ip", "-6", "addr", "add", "2001:db8:19::1/64", "dev", "lan0", "nodad")
    run("ip", "link", "set", "lan0", "up")
    Path("/proc/sys/net/ipv4/ip_forward").write_text("1")
    Path("/proc/sys/net/ipv6/conf/all/forwarding").write_text("1")

    def tell_peer(command):
        peer.stdin.write(command + "\n")
        peer.stdin.flush()
        assert peer.stdout.readline().strip() == "ok"

    def packets(name):
        data = json.loads(run("nft", "-j", "list", "counter", "inet", "namespace_observer", name))
        return next(item["counter"]["packets"] for item in data["nftables"] if "counter" in item)

    def forwarded(address, expected, source=None):
        before, after = packets("before"), packets("after")
        tell_peer(address + (" " + source if source else ""))
        deadline = time.monotonic() + 3
        while packets("before") == before and time.monotonic() < deadline:
            time.sleep(.01)
        assert packets("before") > before, "Test packet never entered the forwarding hook"
        assert (packets("after") > after) == expected, "Unexpected forwarding result"

    def local_connections():
        for address in ('10.20.0.2', '192.168.50.2', '169.254.25.2', 'fd18::2'):
            send_datagram(address)
        for address in ('172.17.0.2', 'fd17::2'):
            with socket.create_connection((address, 11434), timeout=2) as connection:
                connection.sendall(b'GET /api/tags HTTP/1.0\r\n\r\n')
                response = b''
                while data := connection.recv(1024):
                    response += data
                assert response.startswith(b'HTTP/1.1 200 OK') and response.endswith(b'local')
        forwarded('192.168.50.2', True)
        forwarded('fd18::2', True)
        # Destination-only exceptions must not admit public-source forwarded traffic.
        forwarded('192.168.50.2', False, '198.19.0.2')
        forwarded('fd18::2', False, '2001:db8:19::2')
        # Private sources must not exempt an external destination from the kill switch.
        forwarded('198.18.0.2', False)
        forwarded('2001:db8:18::2', False)

    with tempfile.TemporaryDirectory(prefix="cyberghost-ns-") as directory:
        runtime = Path(directory) / "runtime"
        try:
            tell_peer("setup")
            send_datagram("198.18.0.2")
            send_datagram("2001:db8:18::2")
            forwarded("198.18.0.2", True)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as existing:
                send_datagram("198.18.0.2", existing=existing)
                previous = os.umask(0o077)
                try:
                    # The early boot helper holds a lock while starting its
                    # monitor. That child must not retain the lock forever.
                    with (Path(directory) / 'boot.lock').open('w') as boot_lock:
                        fcntl.flock(boot_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        result = authorized_action("block", runtime)
                finally:
                    os.umask(previous)
                with (Path(directory) / 'boot.lock').open('w') as boot_lock:
                    deadline = time.monotonic() + 2
                    while True:
                        try:
                            fcntl.flock(boot_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            assert time.monotonic() < deadline, 'Firewall monitor retained the boot lock'
                            time.sleep(.01)
                assert result.state == "on"
                assert runtime.stat().st_mode & 0o777 == 0o755
                assert request("status", runtime).state == "on"
                blocked("198.18.0.2", existing=existing)
                blocked("198.18.0.2")
                blocked("2001:db8:18::2")
                forwarded("198.18.0.2", False)
                local_connections()
                # At boot there is no tunnel or trusted mark. Block even marked
                # traffic, while allowing DHCP to establish the uplink.
                blocked("198.18.0.2", mark=51820)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dhcp:
                    dhcp.bind(('0.0.0.0', 68))
                    dhcp.sendto(b'namespace DHCP fixture', ('198.18.0.2', 67))
                run("ip", "link", "add", "cyberghost", "type", "wireguard")
                run("wg", "set", "cyberghost", "fwmark", "51820")
                run("ip", "link", "set", "cyberghost", "up")
                run("ip", "route", "replace", "default", "dev", "cyberghost")
                assert authorized_action("enable", runtime).state == "on"
                local_connections()
                # Only the marked UDP outer transport may use the direct path.
                send_datagram("198.18.0.2", mark=51820)
                blocked("198.18.0.2", mark=123)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as loopback:
                    loopback.bind(("127.0.0.1", 0))
                    loopback.settimeout(1)
                    loopback.sendto(b"local", loopback.getsockname())
                    assert loopback.recv(20) == b"local"
            # Altered rules must not still report verified protection.
            run("nft", "add", "rule", "inet", TABLE, "output", "counter")
            assert request("status", runtime).state == "unknown"
            assert authorized_action("enable", runtime).state == "on"
            # A controlled dummy replaces the test WG interface to test the
            # inner output path without a real VPN peer or any internet access.
            run("ip", "link", "del", "cyberghost")
            run("ip", "link", "add", "cyberghost", "type", "dummy")
            run("ip", "link", "set", "cyberghost", "up")
            run("ip", "route", "add", "198.51.100.0/24", "dev", "cyberghost")
            run("ip", "-6", "route", "add", "2001:db8:51::/64", "dev", "cyberghost")
            send_datagram("198.51.100.2")
            send_datagram("2001:db8:51::2")
            forwarded("198.51.100.2", True)
            forwarded("2001:db8:51::2", True)
            run("ip", "link", "del", "cyberghost")
            run("ip", "route", "add", "default", "dev", "plain0")
            blocked("198.51.100.2")
            blocked("2001:db8:51::2")
            forwarded("198.51.100.2", False)
            forwarded("2001:db8:51::2", False)
            local_connections()
            assert request("status", runtime).state == "on", "Protection must survive a vanished tunnel"
            # Exercise the actual kernel cgroup socket match. This namespace has
            # no route to the host/internet and never changes host cgroups.
            group = next(line[3:].lstrip('/') for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
            guard = Firewall()
            # A different cgroup's exception must not allow even this root
            # process's IPv4/IPv6 traffic. Only the selected group may connect.
            if Path('/sys/fs/cgroup/init.scope').is_dir():
                run("nft", "-f", "-", source=connection_rule('init.scope'))
                blocked("198.18.0.2")
                blocked("2001:db8:18::2")
                remove_exception(guard, 'init.scope')
            run("nft", "-f", "-", source=connection_rule(group))
            send_datagram("198.18.0.2")
            send_datagram("2001:db8:18::2")
            forwarded("198.18.0.2", False)
            remove_exception(guard, group)
            blocked("198.18.0.2")
            blocked("2001:db8:18::2")
            assert request("status", runtime).state == "on", "Removing the exception must restore the original rules"
            assert authorized_action("disable", runtime).state == "off"
            send_datagram("198.18.0.2")
            send_datagram("2001:db8:18::2")
            forwarded("198.18.0.2", True)
            assert Firewall().document() is None
            assert not (runtime / "active").exists()
            assert packets("before") > 0, "An unrelated table was removed"
            print("Kernel checks passed: local IPv4/IPv6 TCP services, LAN/Docker forwarding, external output/forward blocking before VPN and after tunnel loss, established connections, DHCP, WireGuard transport, loopback, rule verification and scoped cleanup.", flush=True)
        finally:
            authorized_action("disable", runtime)
            peer.stdin.write("stop\n")
            peer.stdin.flush()
            peer.wait(timeout=3)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        command = ["unshare", "--user", "--map-root-user", "--net", "/usr/bin/python3", __file__,
                   "--inside", os.readlink("/proc/self/ns/net")]
        raise SystemExit(subprocess.call(command))
    if len(sys.argv) == 3 and sys.argv[1] == "--inside":
        inner(sys.argv[2])
    else:
        raise SystemExit("Run without arguments to create isolated test namespaces.")
