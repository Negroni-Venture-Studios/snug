"""Rate limits for signing in. Windows are 15 minutes."""

from snug.core.ratelimit import Limit

WINDOW = 15 * 60

# Attempts from one address, successful or not.
PASSWORD_PER_IP = Limit("login.password.ip", limit=20, window=WINDOW)
CODE_PER_IP = Limit("login.code.ip", limit=20, window=WINDOW)
RECOVERY_PER_IP = Limit("login.recovery.ip", limit=10, window=WINDOW)

# Failures against one account, from anywhere. Generous on purpose: a low limit
# would let anyone lock a founder out by typing wrong passwords, and a correct
# password only reaches the code step.
PASSWORD_PER_ACCOUNT = Limit("login.password.account", limit=30, window=WINDOW)
CODE_PER_ADMIN = Limit("login.code.admin", limit=10, window=WINDOW)
RECOVERY_PER_ADMIN = Limit("login.recovery.admin", limit=5, window=WINDOW)

# Wrong codes allowed after one correct password, before starting again.
CODE_FAILURES_PER_PASSWORD = 5
