"""ใบรับรอง https สำหรับหน้าเว็บซับในเครื่อง (TikTok LIVE Studio รับเฉพาะลิงก์ https)

สร้าง "ผู้ออกใบรับรอง" (CA) ส่วนตัวของเครื่องนี้ ที่ถูกจำกัดให้รับรองได้แค่ชื่อในเครื่อง
(localtest.me, *.nip.io, localhost, 127.0.0.1) — เอาไปปลอมเว็บอื่นไม่ได้ แม้ไฟล์จะหลุด
แล้วติดตั้งเข้า Windows ของผู้ใช้คนนี้ (ไม่ต้องเป็นแอดมิน — Windows จะถามยืนยันหนึ่งครั้ง)

ไฟล์อยู่ใน data/web_cert/ (ไม่ขึ้น git, ไม่อยู่ใน ZIP ที่แจก — แต่ละเครื่องสร้างของตัวเอง)
"""
import datetime
import ipaddress
import subprocess

from app.matcher import ROOT

CERT_DIR = ROOT / "data" / "web_cert"
CA_CERT = CERT_DIR / "ca.crt"
CA_KEY = CERT_DIR / "ca.key"
LEAF_CERT = CERT_DIR / "server.crt"
LEAF_KEY = CERT_DIR / "server.key"
NAMES = ["localtest.me", "127.0.0.1.nip.io", "localhost"]
CA_NAME = "GTA6ThaiSub local subtitles (this PC only)"
_NO_WINDOW = 0x08000000                                   # CREATE_NO_WINDOW: ไม่ให้หน้าต่าง cmd เด้ง


def ensure_cert():
    """สร้าง CA + ใบรับรองเซิร์ฟเวอร์ถ้ายังไม่มี คืน (server.crt, server.key)"""
    if LEAF_CERT.exists() and LEAF_KEY.exists() and CA_CERT.exists():
        return LEAF_CERT, LEAF_KEY
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
    permitted = [x509.DNSName("localtest.me"), x509.DNSName("nip.io"), x509.DNSName("localhost"),
                 x509.IPAddress(ipaddress.ip_network("127.0.0.0/8"))]
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName(n) for n in NAMES] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localtest.me")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=820))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    nokey = serialization.NoEncryption()
    pk8 = serialization.PrivateFormat.PKCS8
    CA_CERT.write_bytes(ca.public_bytes(pem))
    CA_KEY.write_bytes(ca_key.private_bytes(pem, pk8, nokey))
    LEAF_CERT.write_bytes(leaf.public_bytes(pem))
    LEAF_KEY.write_bytes(key.private_bytes(pem, pk8, nokey))
    return LEAF_CERT, LEAF_KEY


def _thumbprint():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    return x509.load_pem_x509_certificate(CA_CERT.read_bytes()).fingerprint(hashes.SHA1()).hex()


def is_installed():
    """CA ของเราอยู่ใน "Trusted Root" ของผู้ใช้ Windows คนนี้แล้วหรือยัง"""
    if not CA_CERT.exists():
        return False
    try:
        r = subprocess.run(["certutil", "-user", "-store", "Root", _thumbprint()], capture_output=True,
                           creationflags=_NO_WINDOW, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def install():
    """ติดตั้ง CA เข้า Windows (Windows จะเด้งกล่องถามยืนยัน) คืน True ถ้าติดตั้งแล้ว"""
    ensure_cert()
    if is_installed():
        return True
    subprocess.run(["certutil", "-user", "-addstore", "Root", str(CA_CERT)], capture_output=True,
                   creationflags=_NO_WINDOW, timeout=180)
    return is_installed()


def uninstall():
    """เอา CA ออกจาก Windows (ปุ่ม "ถอนใบรับรอง")"""
    if CA_CERT.exists():
        subprocess.run(["certutil", "-user", "-delstore", "Root", _thumbprint()], capture_output=True,
                       creationflags=_NO_WINDOW, timeout=60)
    return not is_installed()
