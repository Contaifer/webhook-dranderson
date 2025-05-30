from flask import Flask, request
import json
import os
import time
import gspread
import openai
import requests
import hashlib
import hmac
import random
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

VERIFY_TOKEN = "robodranderson123"
MAX_COMENTARIOS_POR_HORA = 20
MAX_DIRECTS_POR_HORA = 40
INTERACOES_ANTES_CTA = 3
DELAY_ENTRE_RESPOSTAS = 3

respostas_enviadas = {"comentario": {}, "direct": {}}
interacoes_por_usuario = {}
COMENTARIOS_JSON = "comentarios_respondidos.json"

CTAS = [
    "Se quiser conversar melhor, me chama no direct.",
    "Me manda um direct se quiser falar mais sobre isso.",
    "A gente pode continuar essa troca no direct, se fizer sentido pra você.",
    "Se sentir vontade de conversar, estarei no direct.",
    "No direct eu consigo ouvir com mais atenção, se quiser seguir por lá.",
    "Posso te explicar melhor com calma no direct, se quiser.",
    "Se fizer sentido pra você, me chama no direct.",
    "Podemos seguir essa conversa no direct com mais privacidade.",
    "Tô por aqui se quiser continuar essa troca no direct.",
    "Quando quiser, é só me chamar no direct."
]

def carregar_comentarios_respondidos():
    try:
        with open(COMENTARIOS_JSON, "r") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def salvar_comentario_respondido(comment_id):
    comentarios_respondidos.add(comment_id)
    with open(COMENTARIOS_JSON, "w") as f:
        json.dump(list(comentarios_respondidos), f)

comentarios_respondidos = carregar_comentarios_respondidos()

openai.api_key = os.environ["OPENAI_API_KEY"]
INSTAGRAM_TOKEN = os.environ["INSTAGRAM_TOKEN"]
APP_SECRET = os.environ["INSTAGRAM_APP_SECRET"]

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
gc = gspread.authorize(credentials)
sheet = gc.open("webhook_instagram_logs").sheet1

def ler_lista_exclusao():
    try:
        with open("excluir_usuarios.txt", "r") as f:
            return [linha.strip().lower() for linha in f if linha.strip()]
    except FileNotFoundError:
        return []

def classificar_sentimento(texto):
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Classifique a mensagem como: positivo, neutro, negativo, sensível ou agressivo."},
                {"role": "user", "content": texto}
            ],
            temperature=0.3,
            max_tokens=10
        )
        return response.choices[0].message.content.strip().lower()
    except Exception as e:
        print("Erro ao classificar sentimento:", e)
        return "neutro"

def gerar_resposta(texto, sentimento, tipo, interacoes):
    base = ""
    if sentimento == "agressivo":
        base = "Esse tipo de comentário não contribui para um espaço saudável. Convido você a refletir sobre sua abordagem."
    elif sentimento in ["positivo", "neutro", "sensível", "negativo"] and len(texto) < 10:
        base = "Obrigado pelo seu comentário."
    else:
        try:
            prompt = [
                {"role": "system", "content": "Você é um médico especialista em saúde mental e clínica médica. Responda de forma empática, clara, direta e com no máximo 4 linhas. Se o comentário for agressivo, reaja como um humano que discorda de forma firme, mas respeitosa."},
                {"role": "user", "content": texto}
            ]
            response = openai.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=prompt,
                temperature=0.6,
                max_tokens=300
            )
            base = response.choices[0].message.content.strip()
        except Exception as e:
            print("Erro ao gerar resposta generativa:", e)
            base = "Obrigado por comentar."

    if tipo == "comentario":
        base += random.choice(CTAS)

    if tipo == "direct" and interacoes >= INTERACOES_ANTES_CTA:
        base += (
            " Se quiser conversar com alguém da minha equipe, clique aqui: "
            "https://api.whatsapp.com/send?phone=5527996677672&text=Olá!%20Gostaria%20de%20mais%20informações%20sobre%20as%20consultas%20com%20o%20Dr.%20Anderson%20Contaifer"
        )

    return base[:2200] if tipo == "comentario" else base[:1000]

def gerar_appsecret_proof(token, secret):
    return hmac.new(
        key=secret.encode('utf-8'),
        msg=token.encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()

def enviar_resposta_instagram(tipo, username, resposta, comment_id=None):
    try:
        proof = gerar_appsecret_proof(INSTAGRAM_TOKEN, APP_SECRET)

        if tipo == "comentario" and comment_id:
            if comment_id in comentarios_respondidos:
                print(f"🚫 Comentário {comment_id} já foi respondido. Ignorando.")
                return False

            url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
            payload = {
                "message": resposta,
                "access_token": INSTAGRAM_TOKEN,
                "appsecret_proof": proof
            }
            r = requests.post(url, data=payload)

            if r.status_code == 200:
                salvar_comentario_respondido(comment_id)

        elif tipo == "direct" and username:
            url = "https://graph.facebook.com/v19.0/me/messages"
            payload = {
                "messaging_type": "RESPONSE",
                "recipient": {"id": username},
                "message": {"text": resposta},
                "access_token": INSTAGRAM_TOKEN,
                "appsecret_proof": proof
            }
            r = requests.post(url, json=payload)

        else:
            print("⚠️ Tipo inválido ou dados faltando para enviar resposta.")
            return False

        print(f"📤 Status da resposta ({tipo}):", r.status_code, r.text)

        if r.status_code != 200:
            print(f"❌ Falha ao enviar {tipo.upper()}: {r.status_code} - {r.text}")
            return False

        print(f"📤 {tipo.capitalize()} enviado com sucesso: {r.status_code}")
        return True

    except Exception as e:
        print("❌ Erro ao enviar resposta:", e)
        return False

def pode_responder(tipo, username):
    agora = time.time()
    historico = respostas_enviadas[tipo].get(username, [])
    historico = [t for t in historico if agora - t < 3600]
    respostas_enviadas[tipo][username] = historico
    limite = MAX_COMENTARIOS_POR_HORA if tipo == "comentario" else MAX_DIRECTS_POR_HORA
    return len(historico) < limite

def registrar_resposta(tipo, username):
    respostas_enviadas[tipo].setdefault(username, []).append(time.time())

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
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
            comment_id = ""

            if "entry" in data:
                entry = data["entry"][0]
                if "changes" in entry:
                    tipo = "comentario"
                    value = entry["changes"][0]["value"]
                    username = value.get("from", {}).get("username", "").lower()
                    mensagem = value.get("text", "")
                    id_post = value.get("media", {}).get("id", "")
                    comment_id = value.get("id", "")
                    print(f"🧩 Comentário - username: {username}, comment_id: {comment_id}, mensagem: {mensagem}")

                    if username == "drandersoncontaifer":
                        print("🛑 Comentário feito por mim mesmo. Ignorando.")
                        return "Ignorado", 200

                elif "messaging" in entry:
                    tipo = "direct"
                    messaging = entry["messaging"][0]
                    mensagem = messaging.get("message", {}).get("text", "")
                    username = messaging.get("sender", {}).get("id", "")

            sheet.append_row([
                datetime.now().isoformat(), tipo, username, mensagem, id_post, "", json.dumps(data)
            ])

            if username in ler_lista_exclusao():
                print(f"🚫 Usuário ignorado (lista): {username}")
                return "Ignorado", 200

            print(f"✅ Vai tentar responder para: {username}")
            print(f"🧠 Mensagem recebida: {mensagem}")

            if pode_responder(tipo, username):
                interacoes = interacoes_por_usuario.get(username, 0) + 1
                interacoes_por_usuario[username] = interacoes
                sentimento = classificar_sentimento(mensagem)
                resposta = gerar_resposta(mensagem, sentimento, tipo, interacoes)
                print(f"🤖 Resposta ({tipo}): {resposta}")
                time.sleep(DELAY_ENTRE_RESPOSTAS)
                sucesso = enviar_resposta_instagram(tipo, username, resposta, comment_id if tipo == "comentario" else None)
                print(f"📬 Resultado do envio: {sucesso}")
                if sucesso:
                    registrar_resposta(tipo, username)
            else:
                print(f"⚠️ Limite de {tipo}s por hora para {username} atingido. Ignorando.")

        except Exception as e:
            print("❌ Erro geral:", str(e))

    return "OK", 200

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    app.run(host="0.0.0.0", port=8080)
