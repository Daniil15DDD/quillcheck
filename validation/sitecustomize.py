import os, sys
_path = os.environ.get("NET_AUDIT_LOG")
if _path:
    def _hook(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
            with open(_path, "a") as f:
                f.write(f"pid={os.getpid()} {event} {str(args)[:160]}\n")
    sys.addaudithook(_hook)
