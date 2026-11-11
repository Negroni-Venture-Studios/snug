"""deploy/cloud-init.yaml, read as text. CI checks it against cloud-init's schema."""

import hashlib
from pathlib import Path

CLOUD_INIT = (Path(__file__).resolve().parent.parent / "deploy" / "cloud-init.yaml").read_text()


def test_the_server_swaps_only_under_pressure() -> None:
    # Found in the audit of 10 November 2026: the swap file can hold memory
    # with the secret key in it, so the kernel is told to swap reluctantly.
    assert "filename: /swapfile" in CLOUD_INIT
    drop_in = CLOUD_INIT.split("- path: /etc/sysctl.d/60-snug.conf", 1)[1].split("- path:", 1)[0]
    assert "vm.swappiness = 10" in drop_in
    assert "  - sysctl -p /etc/sysctl.d/60-snug.conf" in CLOUD_INIT


# Docker's signing key for its Ubuntu repository (download.docker.com/linux/ubuntu/gpg).
DOCKER_KEY = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"


def fingerprint(armoured: str) -> str:
    """The OpenPGP v4 fingerprint of the first public key in an armoured block."""
    import base64
    import hashlib

    lines = armoured.strip().splitlines()
    body = lines[lines.index("") + 1 :]  # after the armour headers' blank line
    data = base64.b64decode("".join(line for line in body if not line.startswith(("=", "-"))))
    tag = data[0]
    assert tag & 0x80, "not an OpenPGP packet"
    if tag & 0x40:  # new format
        length, start = (
            (data[1], 2) if data[1] < 192 else (((data[1] - 192) << 8) + data[2] + 192, 3)
        )
    else:  # old format: the length's size is in the tag's low two bits
        size = {0: 1, 1: 2, 2: 4}[tag & 0x03]
        length, start = int.from_bytes(data[1 : 1 + size], "big"), 1 + size
    packet = data[start : start + length]
    return hashlib.sha1(b"\x99" + len(packet).to_bytes(2, "big") + packet).hexdigest().upper()  # noqa: S324


def test_dockers_key_is_in_the_file_not_fetched_from_a_keyserver() -> None:
    # Found on 11 November 2026: keyserver.ubuntu.com failed, Docker never
    # installed, and cloud-init still reported no errors.
    source = CLOUD_INIT.split("docker.list:", 1)[1].split("packages:", 1)[0]
    assert "keyid:" not in source and "keyserver" not in source
    block = source.split("key: |", 1)[1].split("-----END PGP PUBLIC KEY BLOCK-----", 1)[0]
    armoured = "\n".join(line.strip() for line in block.strip().splitlines())
    assert armoured.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    assert fingerprint(armoured + "\n") == DOCKER_KEY
    # The whole block, subkey and signatures too, byte for byte as Docker
    # publishes it at download.docker.com/linux/ubuntu/gpg (audit N1).
    whole = armoured + "\n-----END PGP PUBLIC KEY BLOCK-----\n"
    assert hashlib.sha256(whole.encode()).hexdigest() == (
        "1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570"
    )


def test_the_login_message_says_when_docker_is_missing() -> None:
    motd = CLOUD_INIT.split("- path: /etc/update-motd.d/99-snug", 1)[1].split("- path:", 1)[0]
    assert "command -v docker" in motd
    assert "did not finish" in motd


def test_a_server_without_a_release_is_not_set_up_from_main() -> None:
    # Found in the audit of 11 November 2026: falling back to main ran unreleased code.
    assert "ref=main" not in CLOUD_INIT
    fetch = CLOUD_INIT.split("releases/latest", 1)[1].split("for file in", 1)[0]
    assert "exit 1" in fetch


def test_the_login_message_says_when_the_install_did_not_finish() -> None:
    motd = CLOUD_INIT.split("- path: /etc/update-motd.d/99-snug", 1)[1].split("- path:", 1)[0]
    assert "/usr/local/bin/snug" in motd
    assert "did not finish" in motd
    # The guide's address, since the server has no copy of the docs (audit N2).
    assert (
        "https://github.com/Negroni-Venture-Studios/snug/blob/main/docs/deploy-digitalocean.md"
        in motd
    )


def test_the_login_message_says_wait_while_cloud_init_is_still_running() -> None:
    # A console opened during the ten-minute install must not be told the
    # installation failed and to destroy the server.
    motd = CLOUD_INIT.split("- path: /etc/update-motd.d/99-snug", 1)[1].split("- path:", 1)[0]
    running = motd.index("cloud-init status")
    assert running < motd.index("did not finish")
    assert "still installing" in motd
    assert "fifteen" not in motd
