"""Rate limits for the viewer's gate. Windows are 15 minutes unless noted.

The daily cap on code checks per link lives in snug.viewer.codes, so that no
page can skip it.
"""

from snug.core.ratelimit import Limit

WINDOW = 15 * 60

# Asking for a code. Every attempt from an address counts, right or wrong.
SEND_PER_IP = Limit("viewer.send.ip", limit=20, window=WINDOW)
# Wrong addresses typed on one link, from anywhere. The page says plainly
# when an address is wrong (decided 5 November 2026). Past this many, every
# request on the link gets the same neutral reply for the rest of the hour,
# so guessing learns nothing, while the right address still gets a code.
WRONG_EMAIL_PER_LINK = Limit("viewer.wrong_email.link", limit=10, window=60 * 60)
# Codes actually sent: per link, and to one inbox across every link.
SEND_PER_LINK = Limit("viewer.send.link", limit=5, window=WINDOW)
SEND_PER_EMAIL = Limit("viewer.send.email", limit=10, window=60 * 60)

# Entering a code, from one address.
CHECK_PER_IP = Limit("viewer.check.ip", limit=30, window=WINDOW)

# Downloads of one link's document. Each one stamps the whole PDF in a worker.
DOWNLOAD_PER_LINK = Limit("viewer.download.link", limit=30, window=60 * 60)

# Opening a link that needs no verification, from one address.
OPEN_PER_IP = Limit("viewer.open.ip", limit=30, window=WINDOW)
