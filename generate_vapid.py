"""
generate_vapid.py
-----------------
Run once to generate your VAPID key pair and print the values to paste into .env

Usage:
    pip install pywebpush
    python generate_vapid.py
"""

from py_vapid import Vapid
import base64

v = Vapid()
v.generate_keys()

# Export private key as base64url DER
private_bytes = v.private_key.private_bytes(
    encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.DER,
    format=__import__("cryptography").hazmat.primitives.serialization.PrivateFormat.PKCS8,
    encryption_algorithm=__import__("cryptography").hazmat.primitives.serialization.NoEncryption(),
)
private_b64 = base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode()

# Export public key as base64url uncompressed point (what the browser expects)
public_bytes = v.public_key.public_bytes(
    encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.X962,
    format=__import__("cryptography").hazmat.primitives.serialization.PublicFormat.UncompressedPoint,
)
public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode()

print("Add these to your .env file:")
print()
print(f"VAPID_PRIVATE_KEY={private_b64}")
print(f"VAPID_PUBLIC_KEY={public_b64}")
print()
print("The VAPID_PUBLIC_KEY is also what you hardcode in your frontend JS as VAPID_PUBLIC_KEY.")
