"""A stand-in for the egress proxy in tests/test_runtimes.sh: logs the first line of every request (CONNECT host:443 or
GET http://...) to /tmp/proxy.log and refuses it. An agent CLI that contacts anything but its (fake) API shows up here."""
import socketserver


class H(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode(errors="replace").strip()
        with open("/tmp/proxy.log", "a") as f:
            f.write(line + "\n")
        self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")


socketserver.ThreadingTCPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer(("127.0.0.1", 3128), H).serve_forever()
