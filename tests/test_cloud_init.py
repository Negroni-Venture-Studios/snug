"""deploy/cloud-init.yaml, read as text. CI checks it against cloud-init's schema."""

from pathlib import Path

CLOUD_INIT = (Path(__file__).resolve().parent.parent / "deploy" / "cloud-init.yaml").read_text()


def test_the_server_swaps_only_under_pressure() -> None:
    # Found in the audit of 10 November 2026: the swap file can hold memory
    # with the secret key in it, so the kernel is told to swap reluctantly.
    assert "filename: /swapfile" in CLOUD_INIT
    drop_in = CLOUD_INIT.split("- path: /etc/sysctl.d/60-snug.conf", 1)[1].split("- path:", 1)[0]
    assert "vm.swappiness = 10" in drop_in
    assert "  - sysctl -p /etc/sysctl.d/60-snug.conf" in CLOUD_INIT
