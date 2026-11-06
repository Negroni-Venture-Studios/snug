"""Rate limits for signing in. Windows are 15 minutes."""

from snug.core.ratelimit import Limit

WINDOW = 15 * 60

# Attempts from one address, successful or not.
PASSWORD_PER_IP = Limit("login.password.ip", limit=20, window=WINDOW)
CODE_PER_IP = Limit("login.code.ip", limit=20, window=WINDOW)
RECOVERY_PER_IP = Limit("login.recovery.ip", limit=10, window=WINDOW)

# Attempts against one account, from anywhere. Every attempt counts, before
# it is checked, so simultaneous requests cannot slip under; a real admin
# needs one or two. Generous on purpose: a low limit would let anyone lock a
# founder out by typing wrong passwords, and a correct password only reaches
# the code step.
PASSWORD_PER_ACCOUNT = Limit("login.password.account", limit=30, window=WINDOW)
CODE_PER_ADMIN = Limit("login.code.admin", limit=10, window=WINDOW)
RECOVERY_PER_ADMIN = Limit("login.recovery.admin", limit=5, window=WINDOW)

# Wrong codes allowed after one correct password, before starting again.
CODE_FAILURES_PER_PASSWORD = 5

# Confirming an admin change (invite, remove, reset) with a fresh code.
CONFIRM_PER_ADMIN = Limit("admin.confirm.admin", limit=10, window=WINDOW)

# Asking for a forgotten-password link. The reply never says whether the
# address belongs to an admin, and each request may send an email.
FORGOT_PER_IP = Limit("login.forgot.ip", limit=5, window=WINDOW)
FORGOT_PER_ACCOUNT = Limit("login.forgot.account", limit=3, window=WINDOW)

# Codes on the forgotten-password page: their own allowance, so someone holding
# a link cannot use up the admin's sign-in attempts and lock them out. Kept to
# one link's worth, so sign-in and reset together allow 15 guesses in 15
# minutes, not 20. Five wrong codes spend the link (the count is kept on the
# link itself); asking for another is limited above.
RESET_CODE_PER_ADMIN = Limit("admin.reset_code.admin", limit=5, window=WINDOW)
RESET_CODE_FAILURES_PER_LINK = 5

# Submitting the setup page, from one address. Each submission may hash a
# password, which is deliberately slow.
SETUP_PER_IP = Limit("admin.setup.ip", limit=20, window=WINDOW)
