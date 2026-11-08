# gleaner proxy: the only way out of the task network
# it opens HTTPS tunnels (CONNECT) only to the hosts of ALLOWED_HOSTS, and refuses everything else
import os
import socket
import threading

ALLOWED_HOSTS = os.environ["ALLOWED_HOSTS"].split(",")  # the addresses of the current task's buckets, set by the worker
PORT = 3128
MAX_REQUEST_BYTES = 8192  # a CONNECT request is a few lines: anything bigger is refused


def pipe(source, destination):
    # copies the bytes one way until the source closes
    data = source.recv(65536)
    while data:
        destination.sendall(data)
        data = source.recv(65536)
    destination.shutdown(socket.SHUT_WR)  # tells the other side this direction is over


def refuse(client, reason):
    print(f"Refusé : {reason}", flush=True)
    client.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    client.close()


def handle(client):
    # 1. read the request: "CONNECT bucket.s3.amazonaws.com:443 HTTP/1.1", then headers, up to an empty line
    request = b""
    while b"\r\n\r\n" not in request:
        chunk = client.recv(4096)
        if not chunk:
            client.close()
            return
        request += chunk
        if len(request) > MAX_REQUEST_BYTES:
            refuse(client, "requête trop longue")
            return

    # 2. only an HTTPS tunnel to an allowed host
    first_line = request.split(b"\r\n")[0].decode(errors="replace")
    parts = first_line.split(" ")
    if len(parts) != 3:
        refuse(client, first_line)
        return
    method = parts[0]
    target = parts[1]  # "host:port"
    if method != "CONNECT":
        refuse(client, first_line)
        return
    host, _, port = target.rpartition(":")
    if port != "443":
        refuse(client, first_line)
        return
    if host not in ALLOWED_HOSTS:
        refuse(client, first_line)
        return

    # 3. open the tunnel and copy the bytes both ways until the end
    server = socket.create_connection((host, 443))
    client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
    upload = threading.Thread(target=pipe, args=(client, server))
    upload.start()
    pipe(server, client)
    upload.join()
    client.close()
    server.close()


listener = socket.create_server(("0.0.0.0", PORT))
print("ready", flush=True)  # the worker waits for this line before starting the task
while True:
    client, address = listener.accept()
    threading.Thread(target=handle, args=(client,), daemon=True).start()
