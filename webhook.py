from flask import Flask, request
import json
import os
import time
import gspread
import openai
import requests
import random
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

VERIFY_TOKEN = "robodranderson123"
MAX_COMENTARIOS_POR_HORA = 20
MAX_DIRECTS_POR_HORA = 40
INTERACOES_ANTES_CTA = 3

respostas_enviadas = {"comentario": [], "direct": []}
interacoes_por_usuario = {}

# 🔐 Chave da OpenAI
openai.api_key = os.environ["OPENAI_API_KEY"]

# 📊 Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
gc = gspread.authorize(credentials)
sheet = gc.open("webhook_instagram_logs").sheet1

# 📃 Lista de usuários a ignorar
def ler_lista_exclusao():
    try:
        with open("excluir_usuarios.txt", "r") as f:
            return [linha.strip().lower() for linha in f if linha.strip()]
    except FileNotFoundError:
        return []

# 🧠 Classificação de sentimento com IA
def classificar_sentimento(texto):
    prompt = f"Classifique o sentimento da seguinte mensagem como: positivo, neutro, negativo, sensível.\nMensagem: {texto}\nClassificação:"
    try:
        resposta = openai.Completion.create(
            engine="text-davinci-003",
            prompt=prompt,
            max_tokens=10,
            temperature=0.4
        )
        return resposta.choices[0].text.strip().lower()
    except Exception as e:
        print("Erro ao classificar sentimento:", e)
        return "neutro"

# ✍️ Geração da resposta com base no sentimento
def gerar_resposta(texto, sentimento, tipo, interacoes):
    base = ""

    if "consulta" in texto.lower() or "atendimento" in texto.lower():
        base = ("Sou médico especialista em clínica médica (RQE 18790), com 13 anos de formado e experiência como professor de medicina. "
                "Minha abordagem é focada na regulação do sistema nervoso para tratar traumas como o TEPT-C. "
                "As consultas são online, com duração de 1 hora, e visam aliviar sintomas como ansiedade, insônia, tensão muscular, "
                "fadiga e outros efeitos do trauma. Você recebe relatório médico e avaliamos juntos a necessidade de exames ou medicação.")

    elif "não tenho dinheiro" in texto.lower() or "não posso pagar" in texto.lower():
        base = ("Entendo sua dificuldade. Uma alternativa acessível é o curso 'Quebrando as Algemas' em www.quebrandoasalgemas.com.br. "
                "Você pode usar o cupom **MQA50** para 50% de desconto. O acesso é válido por 1 ano e a renovação é automática — "
                "mas você pode cancelar dentro da Hotmart a qualquer momento.")

    elif sentimento == "sensível":
        base = "Li sua mensagem com atenção. Você não está sozinho(a). O que você sente é válido e pode ser cuidado com calma."

    elif sentimento == "negativo":
        base = "Entendo que esteja sendo difícil agora. Se quiser conversar melhor, posso te orientar no que for possível."

    elif sentimento == "positivo":
        base = "Fico muito feliz com sua mensagem! Obrigado pela confiança. Se quiser conversar mais de perto, posso te explicar melhor sobre meu trabalho."

    elif sentimento == "neutro":
        base = "Que bom que você escreveu. Me conta um pouco mais do que você está passando."

    if tipo == "direct" and interacoes >= INTERACOES_ANTES_CTA:
        base += " Se quiser ajuda direta da minha equipe, você pode mandar mensagem aqui: https://api.whatsapp.com/send?phone=5527996677672&text=Olá!%20Gostaria%20de%20mais%20informações%20sobre%20as%20consultas%20com%20o%20Dr.%20Anderson%20Contaifer"

    if tipo == "comentario":
        base = base.replace("www.quebrandoasalgemas.com.br", "link na bio")

    return base[:2200] if tipo == "comentario" else base[:1000]

# 📬 Enviar resposta pela API do Instagram
def enviar_resposta_instagram(tipo, username, mensagem_original, resposta, id_post):
    token = os.environ["INSTAGRAM_TOKEN"]

    if tipo == "comentario" and id_post:
        url = f"https://graph.facebook.com/v18.0/{id_post}/replies"
        payload = {"message": resposta, "access_token": token}
    elif tipo == "direct":
        url = f"https://graph.facebook.com/v18.0/me/messages"
        payload = {
            "messaging_type": "RESPONSE",
            "recipient": {"id": username},
            "message": {"text": resposta},
            "access_token": token
        }
    else:
        print("⚠️ Tipo de resposta desconhecido ou dados incompletos.")
        return

    try:
        resp = requests.post(url, json=payload)
        if resp.status_code == 200:
            print(f"✅ Resposta enviada: {resposta}")
        else:
            print(f"❌ Erro ao enviar ({tipo}):", resp.text)
    except Exception as e:
        print("❌ Erro na requisição:", str(e))

def pode_responder(tipo):
    agora = time.time()
    respostas_enviadas[tipo] = [t for t in respostas_enviadas[tipo] if agora - t < 3600]
    limite = MAX_COMENTARIOS_POR_HORA if tipo == "comentario" else MAX_DIRECTS_POR_HORA
    return len(respostas_enviadas[tipo]) < limite

def registrar_resposta(tipo):
    respostas_enviadas[tipo].append(time.time())

@app.route("/", methods=["GET", "POST", "HEAD"])
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

                if "changes" in entry:
                    tipo = "comentario"
                    value = entry["changes"][0]["value"]
                    username = value.get("from", {}).get("username", "").lower()
                    mensagem = value.get("text", "")
                    id_post = value.get("media", {}).get("id", "")

                elif "messaging" in entry:
                    tipo = "direct"
                    messaging = entry["messaging"][0]
                    mensagem = messaging.get("message", {}).get("text", "")
                    username = messaging.get("sender", {}).get("id", "")

            sheet.append_row([
                datetime.now().isoformat(),
                tipo,
                username,
                mensagem,
                id_post,
                emoji,
                json.dumps(data)
            ])

            if username in ler_lista_exclusao():
                print(f"Usuário {username} está na lista de exclusão. Ignorando.")
                return "Ignorado", 200

            if pode_responder(tipo):
                interacoes = interacoes_por_usuario.get(username, 0) + 1
                interacoes_por_usuario[username] = interacoes
                sentimento = classificar_sentimento(mensagem)
                resposta = gerar_resposta(mensagem, sentimento, tipo, interacoes)
                print(f"🤖 Resposta ({tipo}): {resposta}")
                registrar_resposta(tipo)
                enviar_resposta_instagram(tipo, username, mensagem, resposta, id_post)
            else:
                print(f"⚠️ Limite de {tipo}s por hora atingido. Ignorando.")

        except Exception as e:
            print("Erro geral:", str(e))

    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

