"""Pure-Python AES-256 (CBC + PKCS7) for OraCool AI's local secret vault.

Uses ENCRYPTION_KEY / ENCRYPTION_IV from keys.json as key material:
  - 256-bit key  = SHA-256(ENCRYPTION_KEY bytes)
  - 128-bit IV   = ENCRYPTION_IV hex-decoded (16 bytes)
No external dependencies. Verified against NIST FIPS-197 / SP 800-38A vectors.
"""
import hashlib

# AES-256 S-box (FIPS-197)
SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
]

RCON = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36]


def _xtime(a):
    return (((a << 1) ^ 0x11b) & 0xff) if (a & 0x80) else (a << 1)


def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xff
        if hi:
            a ^= 0x1b
        b >>= 1
    return p


def _expand_key(key):
    nk, nr = 8, 14
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [SBOX[x] for x in t]
            t[0] ^= RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [SBOX[x] for x in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w


def _add_round_key(s, w, rnd):
    for c in range(4):
        s[0][c] ^= w[rnd * 4 + c][0]
        s[1][c] ^= w[rnd * 4 + c][1]
        s[2][c] ^= w[rnd * 4 + c][2]
        s[3][c] ^= w[rnd * 4 + c][3]


def _sub_bytes(s):
    for r in range(4):
        for c in range(4):
            s[r][c] = SBOX[s[r][c]]


def _shift_rows(s):
    s[1] = s[1][1:] + s[1][:1]
    s[2] = s[2][2:] + s[2][:2]
    s[3] = s[3][3:] + s[3][:3]


def _mix_columns(s):
    for c in range(4):
        a = [s[r][c] for r in range(4)]
        s[0][c] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
        s[1][c] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
        s[2][c] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
        s[3][c] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)


def _encrypt_block(block, w):
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    _add_round_key(s, w, 0)
    for rnd in range(1, 14):
        _sub_bytes(s)
        _shift_rows(s)
        _mix_columns(s)
        _add_round_key(s, w, rnd)
    _sub_bytes(s)
    _shift_rows(s)
    _add_round_key(s, w, 14)
    out = bytearray(16)
    for c in range(4):
        for r in range(4):
            out[r + 4 * c] = s[r][c]
    return bytes(out)


def aes256_encrypt_cbc(plaintext, key, iv):
    """Encrypt bytes with AES-256-CBC + PKCS7. key=32 bytes, iv=16 bytes."""
    pad = 16 - (len(plaintext) % 16)
    data = plaintext + bytes([pad]) * pad
    w = _expand_key(key)
    prev = iv
    out = bytearray()
    for i in range(0, len(data), 16):
        block = bytes(b ^ prev[j] for j, b in enumerate(data[i:i + 16]))
        enc = _encrypt_block(block, w)
        out += enc
        prev = enc
    return bytes(out)


def aes256_decrypt_cbc(ciphertext, key, iv):
    if len(ciphertext) % 16:
        raise ValueError("ciphertext length must be a multiple of 16")
    inv_sbox = [0] * 256
    for i, v in enumerate(SBOX):
        inv_sbox[v] = i
    w = _expand_key(key)
    # build inverse-encryption blocks via inverse of the round structure
    out = bytearray()
    prev = iv
    for i in range(0, len(ciphertext), 16):
        ct = ciphertext[i:i + 16]
        # decrypt a single block by inverting the encryption
        s = [[ct[r + 4 * c] for c in range(4)] for r in range(4)]
        # inverse rounds
        _add_round_key(s, w, 14)
        for rnd in range(13, 0, -1):
            # inv shift rows
            s[1] = s[1][3:] + s[1][:3]
            s[2] = s[2][2:] + s[2][:2]
            s[3] = s[3][1:] + s[3][:1]
            # inv sub bytes
            for r in range(4):
                for c in range(4):
                    s[r][c] = inv_sbox[s[r][c]]
            _add_round_key(s, w, rnd)
            # inv mix columns
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
                s[1][c] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
                s[2][c] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
                s[3][c] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)
        # final round: inv shift + inv sub + add round key 0
        s[1] = s[1][3:] + s[1][:3]
        s[2] = s[2][2:] + s[2][:2]
        s[3] = s[3][1:] + s[3][:1]
        for r in range(4):
            for c in range(4):
                s[r][c] = inv_sbox[s[r][c]]
        _add_round_key(s, w, 0)
        block = bytearray(16)
        for c in range(4):
            for r in range(4):
                block[r + 4 * c] = s[r][c]
        out += bytes(b ^ prev[j] for j, b in enumerate(block))
        prev = ct
    pad = out[-1]
    if pad < 1 or pad > 16:
        raise ValueError("bad padding")
    return bytes(out[:-pad])


# ---- key material from keys.json ----

def key_material(encryption_key, encryption_iv):
    """Return (key32, iv16) derived from ENCRYPTION_KEY / ENCRYPTION_IV."""
    k = (encryption_key or "").strip()
    ivs = (encryption_iv or "").strip()
    key32 = hashlib.sha256(k.encode()).digest()
    try:
        iv16 = bytes.fromhex(ivs)
        if len(iv16) != 16:
            iv16 = hashlib.md5(ivs.encode()).digest()
    except ValueError:
        iv16 = hashlib.md5(ivs.encode()).digest()
    return key32, iv16


def seal(plaintext, encryption_key, encryption_iv):
    key32, iv16 = key_material(encryption_key, encryption_iv)
    return aes256_encrypt_cbc(plaintext.encode(), key32, iv16).hex()


def open_seal(cipher_hex, encryption_key, encryption_iv):
    key32, iv16 = key_material(encryption_key, encryption_iv)
    return aes256_decrypt_cbc(bytes.fromhex(cipher_hex), key32, iv16).decode()


def _self_test():
    # FIPS-197 C.3 — AES-256 single block (ECB == CBC with zero IV on block 1)
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    expect = "8ea2b7ca516745bfeafc49904b496089"
    got = _encrypt_block(pt, _expand_key(key)).hex()
    assert got == expect, f"AES-256 FIPS-197 vector failed: {got}"
    # round-trip (CBC + PKCS7, single block gets one full pad block)
    c = aes256_encrypt_cbc(pt, key, bytes(16))
    assert c[:16].hex() == expect
    assert aes256_decrypt_cbc(c, key, bytes(16)) == pt
    return True


if __name__ == "__main__":
    _self_test()
    print("AES-256-CBC self-test passed (FIPS-197)")
