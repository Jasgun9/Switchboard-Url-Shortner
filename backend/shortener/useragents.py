from user_agents import parse

from shortener.models import ClickEvent

MAX_STORED_LENGTH = 400


def parse_user_agent(raw):
    # Turn a User-Agent string into the few buckets analytics actually shows.
    if not raw:
        return {"device": ClickEvent.Device.UNKNOWN, "browser": "", "os": ""}

    agent = parse(raw[:MAX_STORED_LENGTH])

    if agent.is_bot:
        device = ClickEvent.Device.BOT
    elif agent.is_tablet:
        device = ClickEvent.Device.TABLET
    elif agent.is_mobile:
        device = ClickEvent.Device.MOBILE
    elif agent.is_pc:
        device = ClickEvent.Device.DESKTOP
    else:
        device = ClickEvent.Device.UNKNOWN

    return {
        "device": device,
        "browser": _clean(agent.browser.family),
        "os": _clean(agent.os.family),
    }


def _clean(family):
    # ua-parser says "Other" when it gives up. Empty groups better in the
    # analytics queries.
    return "" if not family or family == "Other" else family[:40]
