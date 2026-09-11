"""
Shared identity handling for all connectors.

Real identity (login/email) is kept alongside a hashed anon_id: the
self/manager view needs to show a person their own name, but any
cross-person aggregate (team efficiency, "who's active") should key
off anon_id so it's not accidentally another ranking mechanism.
"""
import hashlib


def anon_id_for_email(email, salt="change_this_secret_salt"):
    h = hashlib.sha256()
    h.update(((email or "").lower()).encode("utf-8"))
    h.update(salt.encode("utf-8"))
    return h.hexdigest()


def build_identity(*, login=None, email=None, display_name=None):
    return {
        "login": login,
        "email": email,
        "display_name": display_name,
        "anon_id": anon_id_for_email(email or login),
    }
