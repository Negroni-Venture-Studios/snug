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
# Codes actually sent: to one address on a link, and to one inbox across
# every link.
SEND_PER_LINK = Limit("viewer.send.link", limit=5, window=WINDOW)
SEND_PER_EMAIL = Limit("viewer.send.email", limit=10, window=60 * 60)
# A link for a domain admits any address there, so it could be used to email
# a firm's staff: each link sends codes to at most this many different
# addresses an hour (issue #174). An address is new on the link the first time
# in the hour it is sent a code.
NEW_ADDRESSES_PER_LINK = Limit("viewer.send.new_address.link", limit=10, window=60 * 60)
ADDRESS_SEEN_ON_LINK = Limit("viewer.send.address_seen", limit=1, window=60 * 60)

# Entering a code, from one address.
CHECK_PER_IP = Limit("viewer.check.ip", limit=30, window=WINDOW)

# Downloads of one link's document. Each one stamps the whole PDF in a worker.
DOWNLOAD_PER_LINK = Limit("viewer.download.link", limit=30, window=60 * 60)

# Reader heartbeats, from one address: one every 15 seconds per open tab is
# about 60 in 15 minutes, so this allows ten tabs at once.
BEAT_PER_IP = Limit("viewer.beat.ip", limit=600, window=WINDOW)

# A UK-only link opened from abroad is refused every time, and audited once an
# hour for each address and link (snug.viewer.views._first_refusal).
OUTSIDE_UK_AUDIT = Limit("viewer.outside_uk.audit", limit=1, window=60 * 60)

# Opening a link that needs no verification, from one address.
OPEN_PER_IP = Limit("viewer.open.ip", limit=30, window=WINDOW)
