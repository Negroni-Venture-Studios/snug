"""Free email services, which a link may not admit as a whole domain (issue #174).

A link for gmail.com would open for anyone who can make an account there, so
the link form refuses these domains and asks for the addresses instead. The
list holds the large providers, kept by hand: a founder types the domain, so
an obscure provider is unlikely to come up, and Snug needs no data file to
keep current.
"""

FREE_MAIL = frozenset(
    {
        "126.com",
        "163.com",
        "aim.com",
        "aol.com",
        "btinternet.com",
        "fastmail.com",
        "free.fr",
        "gmail.com",
        "gmx.com",
        "gmx.de",
        "gmx.net",
        "googlemail.com",
        "hey.com",
        "hotmail.co.uk",
        "hotmail.com",
        "hotmail.fr",
        "hushmail.com",
        "icloud.com",
        "laposte.net",
        "libero.it",
        "live.co.uk",
        "live.com",
        "mac.com",
        "mail.com",
        "mail.ru",
        "me.com",
        "msn.com",
        "orange.fr",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "qq.com",
        "rocketmail.com",
        "sky.com",
        "t-online.de",
        "talktalk.net",
        "tuta.io",
        "tutanota.com",
        "virginmedia.com",
        "web.de",
        "yahoo.co.uk",
        "yahoo.com",
        "yahoo.fr",
        "yandex.com",
        "yandex.ru",
        "ymail.com",
        "zoho.com",
        "zohomail.com",
    }
)
