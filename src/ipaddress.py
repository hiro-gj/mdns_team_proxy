class IPv4Address:
    def __init__(self, address):
        self.address = str(address).strip()
        
    @property
    def is_loopback(self):
        return self.address.startswith("127.") or self.address == "::1"

class IPv6Address:
    def __init__(self, address):
        self.address = str(address).strip()
        
    @property
    def is_loopback(self):
        return self.address == "::1" or self.address.startswith("0:0:0:0:0:0:0:1")

def ip_address(address):
    addr_str = str(address)
    if ":" in addr_str:
        return IPv6Address(address)
    else:
        parts = addr_str.split('.')
        if len(parts) == 4:
            try:
                if all(0 <= int(p) <= 255 for p in parts):
                    return IPv4Address(address)
            except ValueError:
                pass
        raise ValueError(f"{address} does not appear to be an IPv4 or IPv6 address")