import random

def uuid4():
    hex_chars = "0123456789abcdef"
    def r_hex(n):
        return "".join(random.choice(hex_chars) for _ in range(n))
    return f"{r_hex(8)}-{r_hex(4)}-4{r_hex(3)}-a{r_hex(3)}-{r_hex(12)}"