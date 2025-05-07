from flask import Flask, request
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

app = Flask(__name__)

VERIFY_TOKEN = "robodranderson123"

# Conectar com Google Sheets via variável de ambiente
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
gc = gspread.authorize(credentials)
sheet = gc.open("webhook_instagram_logs").sheet1

@app.route("/", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return "Unauthorized", 403

    if request.method == "POST":
        data = request.get_json()
        print("🔔 Evento recebido:")
        print(json.dumps(data, indent=2))

        try:
            tipo = "desconhecido"
            username = ""
            mensagem = ""
            id_post = ""
            emoji = ""

            if "entry" in data:
                entry = data["entry"][0]

                # Comentários
                if "changes" in entry:
                    tipo = "comment"
                    change = entry["changes"][0]
                    value = change.get("value", {})
                    username = value.get("from", {}).get("username", "")
                    mensagem = value.get("text", "")
                    id_post = value.get("media", {}).get("id", "")

                # Mensagens diretas
                elif "messaging" in entry:
                    tipo = "message"
                    messaging = entry["messaging"][0]
                    mensagem_data = messaging.get("message", {})
                    mensagem = mensagem_data.get("text", "")
                    emoji = mensagem_data.get("emoji", "") or mensagem_data.get("reaction", {}).get("emoji", "")
                    username = messaging.get("sender", {}).get("id", "")

            # Adicionar linha na planilha
            row = [
                datetime.now().isoformat(),
                tipo,
                username,
                mensagem,
                id_post,
                emoji,
                json.dumps(data)
            ]
            sheet.append_row(row)
        except Exception as e:
            print("Erro ao processar ou salvar evento:", str(e))

        return "Evento recebido", 200

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
