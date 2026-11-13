# The LibreOffice converter (docs/office.md): turning it on and off, and
# installing gVisor's runsc, which it runs under. Sourced by setup.sh and
# update.sh, from this folder, as root.

# gVisor's release, pinned to the newest at least two weeks old (AGENTS.md).
# snug update installs a newer one when a Snug release moves this.
GVISOR_RELEASE=20261026

# gVisor's signing key, fingerprint 6F1D F85E 3A71 C249 18E7 27D5 6FC6 D554 E32B D943,
# kept here so the server never has to fetch it.
GVISOR_KEY='
-----BEGIN PGP PUBLIC KEY BLOCK-----

mQINBF0meAYBEACcBYPOSBiKtid+qTQlbgKGPxUYt0cNZiQqWXylhYUT4PuNlNx5
s+sBLFvNTpdTrXMmZ8NkekyjD1HardWvebvJT4u+Ho/9jUr4rP71cNwNtocz/w8G
DsUXSLgH8SDkq6xw0L+5eGc78BBg9cOeBeFBm3UPgxTBXS9Zevoi2w1lzSxkXvjx
cGzltzMZfPXERljgLzp9AAfhg/2ouqVQm37fY+P/NDzFMJ1XHPIIp9KJl/prBVud
jJJteFZ5sgL6MwjBQq2kw+q2Jb8Zfjl0BeXDgGMN5M5lGhX2wTfiMbfo7KWyzRnB
RpSP3BxlLqYeQUuLG5Yx8z3oA3uBkuKaFOKvXtiScxmGM/+Ri2YM3m66imwDhtmP
AKwTPI3Re4gWWOffglMVSv2sUAY32XZ74yXjY1VhK3bN3WFUPGrgQx4X7GP0A1Te
lzqkT3VSMXieImTASosK5L5Q8rryvgCeI9tQLn9EpYFCtU3LXvVgTreGNEEjMOnL
dR7yOU+Fs775stn6ucqmdYarx7CvKUrNAhgEeHMonLe1cjYScF7NfLO1GIrQKJR2
DE0f+uJZ52inOkO8ufh3WVQJSYszuS3HCY7w5oj1aP38k/y9zZdZvVvwAWZaiqBQ
iwjVs6Kub76VVZZhRDf4iYs8k1Zh64nXdfQt250d8U5yMPF3wIJ+c1yhxwARAQAB
tCpUaGUgZ1Zpc29yIEF1dGhvcnMgPGd2aXNvci1ib3RAZ29vZ2xlLmNvbT6JAk4E
EwEKADgCGwMFCwkIBwIGFQoJCAsCBBYCAwECHgECF4AWIQRvHfheOnHCSRjnJ9Vv
xtVU4yvZQwUCYO4TxQAKCRBvxtVU4yvZQ9UoEACLPV7CnEA2bjCPi0NCWB/Mo1WL
evqv7Wv7vmXzI1K9DrqOhxuamQW75SVXg1df0hTJWbKFmDAip6NEC2Rg5P+A8hHj
nW/VG+q4ZFT662jDhnXQiO9L7EZzjyqNF4yWYzzgnqEu/SmGkDLDYiUCcGBqS2oE
EQfk7RHJSLMJXAnNDH7OUDgrirSssg/dlQ5uAHA9Au80VvC5fsTKza8b3Aydw3SV
iB8/Yuikbl8wKbpSGiXtR4viElXjNips0+mBqaUk2xpqSBrsfN+FezcInVXaXFeq
xtpq2/3M3DYbqCRjqeyd9wNi92FHdOusNrK4MYe0pAYbGjc65BwH+F0T4oJ8ZSJV
lIt+FZ0MqM1T97XadybYFsJh8qvajQpZEPL+zzNncc4f1d80e7+lwIZV/al0FZWW
Zlp7TpbeO/uW+lHs5W14YKwaQVh1whapKXTrATipNOOSCw2hnfrT8V7Hy55QWaGZ
f4/kfy929EeCP16d/LqOClv0j0RBr6NhRBQ0l/BE/mXjJwIk6nKwi+Yi4ek1ARi6
AlCMLn9AZF7aTGpvCiftzIrlyDfVZT5IX03TayxRHZ4b1Rj8eyJaHcjI49u83gkr
4LGX08lEawn9nxFSx4RCg2swGiYw5F436wwwAIozqJuDASeTa3QND3au5v0oYWnl
umDySUl5wPaAaALgzA==
=5/8T
-----END PGP PUBLIC KEY BLOCK-----
'

# The converter needs 2 GB of memory alongside Snug (docs/office.md).
OFFICE_MEMORY_KB=1800000
OFFICE_MEMINFO="${OFFICE_MEMINFO:-/proc/meminfo}"
# Where the system's files are: / on a server, a folder of its own in tests.
OFFICE_ROOT="${OFFICE_ROOT:-}"

office_say() { printf '%s\n' "$*"; }

# office_env KEY VALUE: replace KEY's line in snug.env, as setup.sh's set_env does.
office_env() {
	kept=$(grep -v "^$1=" snug.env || true)
	printf "%s\n%s='%s'\n" "$kept" "$1" "$2" > snug.env
}

# Whether snug.env has the converter turned on.
office_is_on() { grep -q "^SNUG_OFFICE='true'" snug.env 2> /dev/null; }

# This server's memory in MB.
office_memory_mb() { awk '/^MemTotal:/ { print int($2 / 1024) }' "$OFFICE_MEMINFO"; }

# Whether this server has the memory the converter needs.
office_memory_ok() { [ "$(awk '/^MemTotal:/ { print $2 }' "$OFFICE_MEMINFO")" -ge "$OFFICE_MEMORY_KB" ]; }

# Install runsc from gVisor's own packages, the pinned release, and register it with Docker.
office_install_gvisor() {
	if command -v runsc > /dev/null 2>&1 && runsc --version 2> /dev/null | grep -q "release-$GVISOR_RELEASE"; then
		return 0
	fi
	office_say "Installing gVisor $GVISOR_RELEASE, which the converter runs under..."
	keyring=/usr/share/keyrings/gvisor-archive-keyring.gpg
	printf '%s\n' "$GVISOR_KEY" | gpg --dearmor --yes -o "$OFFICE_ROOT$keyring"
	printf 'deb [arch=%s signed-by=%s] https://storage.googleapis.com/gvisor/releases %s main\n' \
		"$(dpkg --print-architecture)" "$keyring" "$GVISOR_RELEASE" > "$OFFICE_ROOT/etc/apt/sources.list.d/gvisor.list"
	apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install --yes -qq runsc
	runsc install
	# Docker reads its runtimes when it starts; Snug's containers restart with it.
	systemctl restart docker
}

# Turn the converter on: gVisor, then the settings Compose and Snug read.
office_on() {
	office_install_gvisor
	office_env SNUG_OFFICE true
	office_env COMPOSE_PROFILES office
}

# Turn the converter off, and remove its container. Its two folders stay, empty.
office_off() {
	office_env SNUG_OFFICE false
	office_env COMPOSE_PROFILES ""
	docker compose --profile office rm --stop --force office > /dev/null 2>&1 || true
}
