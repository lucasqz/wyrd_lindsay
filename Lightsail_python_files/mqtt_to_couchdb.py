import paho.mqtt.client as mqtt
import requests
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from twilio.base.exceptions import TwilioException

from notification import send_message

COUCHDB_URL = "http://admin:password@127.0.0.1:5984"
# COUCHDB_URL = "http://admin:wyrd@127.0.0.1:5984"
DATABASE = "mqtt_data"
MQTT_BROKER = "127.0.0.1"
MQTT_TOPIC = "#"

BR_TZ = ZoneInfo("America/Sao_Paulo")

# --- mapa de status usado em monitorStatus ---
STATUS_MAP = {
    "0": "OK",
    "1": "Alarmado",
    "2": "Reconhecido",
    "3": "Resolvido",
    "4": "Desconhecido",
    "5": "Desconhecido",
    "6": "Desconhecido",
    "7": "Desconhecido",
    "8": "Desconhecido",
    "9": "Desconhecido",
}


def fmt_ts(dt: datetime) -> str:
    """
    Converte um datetime (naive ou aware) para fuso de Brasília,
    remove microssegundos e retorna ISO-format até os segundos.
    """
    # se for naive, assumimos que já veio em horário local de SP
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BR_TZ)
    else:
        dt = dt.astimezone(BR_TZ)

    return dt.strftime("%H:%M:%S %d/%m/%Y")


def create_db():
    """Cria o DB caso não exista."""
    resp = requests.put(f"{COUCHDB_URL}/{DATABASE}")
    if resp.status_code not in (201, 412):
        print(f"[WARN] criação do DB retornou {resp.status_code}")
    else:
        print(f"[OK] DB '{DATABASE}' pronto (status {resp.status_code})")


def parse_message(message: str) -> dict:
    """
    Porta aqui o parseMessage.js:
    - split em ';'
    - parse de timestamp 'YYYY-MM-DD HH:mm:ss'
    - 4 tipos de mensagem
    """
    parts = message.strip().split(";")
    if len(parts) < 2:
        raise ValueError(f"Formato inválido: menos de duas partes ({message!r})")

    irrigador_id = parts[0]
    raw_ts = parts[1]
    # converter "2025-05-26 14:12:00" → "2025-05-26T14:12:00"
    ts_iso = raw_ts.replace(" ", "T")
    try:
        timestamp = datetime.fromisoformat(ts_iso)
    except ValueError:
        raise ValueError(f"Timestamp inválido: {raw_ts}")

    # 1) monitorStatus: >=3 partes e todas as partes[2:] são dígitos únicos
    if len(parts) >= 3 and all(re.fullmatch(r"\d", p) for p in parts[2:]):
        status = [
            {"mt": i + 1, "status": STATUS_MAP.get(p, "Desconhecido")}
            for i, p in enumerate(parts[2:])
        ]
        return {
            "type": "monitorStatus",
            "irrigadorId": irrigador_id,
            "timestamp": fmt_ts(timestamp),
            "status": status,
        }

    # 2) event/alarme: exatamente 3 partes e parts[2] começa com A ou E seguido de dígitos
    if len(parts) == 3 and re.fullmatch(r"[AE]\d+", parts[2]):
        ev = parts[2]
        return {
            "type": "event",
            "irrigadorId": irrigador_id,
            "timestamp": fmt_ts(timestamp),
            "eventType": "alarme" if ev[0] == "A" else "evento",
            "eventCode": ev[1:],
            "status": "Não resolvido",
            "description": "Sem descrição",  # você pode modificar isso mais tarde com informações adicionais
            "responsible": "A definir",  # também pode ser alterado conforme o caso
            "notifications": {
                "WhatsApp": False,
                "E-mail": False,
                "SMS": False,
                "Ligação": False,
            },
            "snooze_time": None,  # isso pode ser ajustado conforme a lógica
            "snooze_until": None,  # ajusta o horário da soneca
        }

    # 3) command simples: exatamente 2 partes e a segunda é só letras
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z]+", parts[1]):
        return {
            "type": "command",
            "irrigadorId": irrigador_id,
            "command": parts[1].replace("'", ""),
            "timestamp": fmt_ts(timestamp),
        }

    # 4) mtTension: >=3 partes → tentamos regex (\d+\.\d{2})([01]) senão float simples
    if len(parts) >= 3:
        mt_readings = []
        for i, token in enumerate(parts[2:]):
            m = re.fullmatch(r"(\d+\.\d{2})([01])", token)
            if m:
                vol = float(m.group(1))
                st = "ON" if m.group(2) == "0" else "OFF"
            else:
                try:
                    vol = float(token)
                except ValueError:
                    vol = 0.0
                st = "–"
            mt_readings.append({"mt": i + 1, "voltage": vol, "status": st})
        return {
            "type": "mtTension",
            "irrigadorId": irrigador_id,
            "timestamp": fmt_ts(timestamp),
            "mtReadings": mt_readings,
        }

    # se não entrou em nenhum caso
    raise ValueError(f"Formato não reconhecido: {message!r}")


def on_message(client, userdata, msg):
    if msg.topic.startswith("$SYS/"):
        return

    payload_str = msg.payload.decode("utf-8", errors="ignore")
    if msg.topic.startswith("lindsay/comandos"):
        return

    try:
        parsed = parse_message(payload_str)
    except ValueError as e:
        # fallback: envia o payload cru + erro
        parsed = {
            "type": "raw",
            "payload": payload_str,
            "error": str(e),
            "timestamp": fmt_ts(datetime.now()),
        }

    doc = {"topic": msg.topic, "origin": "esp32", **parsed}

    # Envia notificação
    to = [
        "+5511995480383",  # Lucas p/ teste
        "+5519974134215",  # Cliente
        "+5511989890203",  # Henrique
    ]

    msg = f"""
*Alarme Acionado*

*ID do Irrigador:* {doc["irrigadorId"]}
*ID do evento:* {doc["eventCode"]}
*Horário:* {doc["timestamp"]}
*Status:* {doc["status"]}
*Descrição:* {doc["description"]}
*Responsável:* {doc["responsible"]}
    """

    if doc["eventType"] == "alarme":
        try:
            send_message(msg, to)
            print("[OK] Notificação não enviada")
        except TwilioException as e:
            print(f"[ERRO] Erro de configuração do Twilio: {e}")
            print(
                "\n[ERRO] Certifique-se de que as variáveis de ambiente TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN estão definidas."
            )
            print("[ERRO] Notificação não enviada")

    # grava no CouchDB
    resp = requests.post(f"{COUCHDB_URL}/{DATABASE}", json=doc)
    if resp.status_code not in (201, 202):
        print(f"[ERRO] POST no CouchDB retornou {resp.status_code}: {resp.text}")
    else:
        print(f"[OK] Gravou doc id={resp.json().get('id')} tipo={parsed['type']}")


if __name__ == "__main__":
    print("Inicializando MQTT → CouchDB…")
    create_db()

    client = mqtt.Client()
    client.on_message = on_message
    client.connect(MQTT_BROKER, 1883, 60)
    client.subscribe(MQTT_TOPIC)
    client.loop_forever()
