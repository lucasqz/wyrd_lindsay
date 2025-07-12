from twilio.rest import Client

from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_message(msg: str, to: list[str]):
    for t in to:
        _ = client.messages.create(
            body=msg,
            from_="whatsapp:+14155238886",
            to=f"whatsapp:{t}",
        )
