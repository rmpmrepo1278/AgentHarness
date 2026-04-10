"""Auto-generate and persist secrets for compose templates."""
import os
import secrets as _secrets
import string
import logging

log = logging.getLogger("secrets_mgr")

_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/secrets")
_SECRET_KEYWORDS = {"SECRET", "PASSWORD", "KEY", "TOKEN", "PASSPHRASE"}


def _is_secret_var(var_name: str) -> bool:
    upper = var_name.upper()
    return any(kw in upper for kw in _SECRET_KEYWORDS)


def _generate_secret(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(_secrets.choice(alphabet) for _ in range(length))


def _secrets_file(stack_name: str) -> str:
    return os.path.join(_SECRETS_DIR, f"{stack_name}.env")


def load_secrets(stack_name: str) -> dict:
    path = _secrets_file(stack_name)
    result = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    result[key.strip()] = val.strip()
    return result


def _save_secrets(stack_name: str, secrets_dict: dict):
    os.makedirs(_SECRETS_DIR, exist_ok=True)
    path = _secrets_file(stack_name)
    with open(path, "w") as f:
        f.write(f"# Auto-generated secrets for {stack_name}\n")
        for key, val in secrets_dict.items():
            f.write(f"{key}={val}\n")
    os.chmod(path, 0o600)


def resolve_secrets(stack_name: str, template_vars: dict) -> dict:
    """Resolve secrets: reuse persisted, auto-generate missing, user values take precedence."""
    persisted = load_secrets(stack_name)
    result = dict(template_vars)
    new_secrets = {}

    for var_name, var_value in result.items():
        if var_value and var_value != "changeme":
            continue
        if _is_secret_var(var_name):
            if var_name in persisted:
                result[var_name] = persisted[var_name]
                log.debug(f"Reusing persisted secret: {var_name}")
            else:
                generated = _generate_secret()
                result[var_name] = generated
                new_secrets[var_name] = generated
                log.info(f"Generated new secret for: {var_name}")

    if new_secrets:
        all_secrets = {**persisted, **new_secrets}
        _save_secrets(stack_name, all_secrets)

    return result
