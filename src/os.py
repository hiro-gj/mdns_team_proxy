from uos import *
import uos

class DummyPath:
    def join(self, *args):
        return "/".join(args).replace("//", "/")
    def dirname(self, path):
        parts = path.rstrip("/").split("/")
        if len(parts) <= 1:
            return ""
        return "/".join(parts[:-1])
    def exists(self, path):
        try:
            uos.stat(path)
            return True
        except:
            return False
    def abspath(self, path):
        return path

path = DummyPath()

def makedirs(path, exist_ok=False):
    parts = path.strip("/").split("/")
    curr = ""
    for p in parts:
        if not p:
            continue
        curr += "/" + p
        try:
            uos.mkdir(curr)
        except:
            pass