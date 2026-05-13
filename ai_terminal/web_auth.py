import getpass
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


AUTH_DIR = Path.home() / ".devserver"
AUTH_FILE = AUTH_DIR / "web_auth.json"


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        260_000,
    ).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password, stored_hash):
    try:
        algorithm, salt, expected = stored_hash.split("$", 2)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    actual = hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


def load_credentials():
    env_user = os.environ.get("DEVSERVER_WEB_USER")
    env_password = os.environ.get("DEVSERVER_WEB_PASSWORD")
    if env_user and env_password:
        return {
            "accounts": {
                env_user: {
                    "password_hash": hash_password(env_password),
                }
            }
        }

    if not AUTH_FILE.exists():
        return None

    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def normalize_credentials(credentials):
    if not credentials:
        return {"accounts": {}}

    if "accounts" in credentials and isinstance(credentials["accounts"], dict):
        return credentials

    username = credentials.get("username")
    password_hash = credentials.get("password_hash")
    if username and password_hash:
        return {"accounts": {username: {"password_hash": password_hash}}}

    return {"accounts": {}}


def save_credentials(credentials):
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(credentials, indent=2), encoding="utf-8")
    try:
        AUTH_FILE.chmod(0o600)
    except OSError:
        pass
    return AUTH_FILE


def credentials_ready():
    credentials = normalize_credentials(load_credentials())
    return bool(credentials["accounts"])


def authenticate(username, password):
    username = (username or "").strip()
    credentials = normalize_credentials(load_credentials())
    account = None
    for stored_username, stored_account in credentials["accounts"].items():
        if stored_username.lower() == username.lower():
            account = stored_account
            break

    if not account:
        return False

    return verify_password(password, account.get("password_hash", ""))


def list_accounts():
    credentials = normalize_credentials(load_credentials())
    return sorted(credentials["accounts"])


def create_account(username, password, overwrite=False):
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    if not password:
        raise ValueError("Password is required.")

    credentials = normalize_credentials(load_credentials())
    if username in credentials["accounts"] and not overwrite:
        raise ValueError(f"Account '{username}' already exists.")

    credentials["accounts"][username] = {"password_hash": hash_password(password)}
    return save_credentials(credentials)


def delete_account(username):
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")

    credentials = normalize_credentials(load_credentials())
    if username not in credentials["accounts"]:
        raise ValueError(f"Account '{username}' does not exist.")

    del credentials["accounts"][username]
    return save_credentials(credentials)


def update_password(username, password):
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    if not password:
        raise ValueError("Password is required.")

    credentials = normalize_credentials(load_credentials())
    if username not in credentials["accounts"]:
        raise ValueError(f"Account '{username}' does not exist.")

    credentials["accounts"][username]["password_hash"] = hash_password(password)
    return save_credentials(credentials)


def set_user(username=None):
    username = username or input("Username: ").strip()
    if not username:
        raise ValueError("Username is required.")

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise ValueError("Passwords do not match.")
    if not password:
        raise ValueError("Password is required.")

    return create_account(username, password, overwrite=True)


def main():
    path = set_user()
    print(f"Saved DevServer web credentials to {path}")


if __name__ == "__main__":
    main()
