from flask import Flask, request
import json
import os
import time
import gspread
import openai
import requests
import hashlib
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

VERIFY_TOKEN = "robodranderson123"
MAX_COMENTARIOS_POR_HORA = 20
MAX_DIRECTS_POR_HORA = 40
INTERACOES_ANTES_CTA = 3
DELAY_ENTRE_RESPOSTAS = 3  # segundos

respostas_enviadas = {"comentario": {}, "direct": {}}
interacoes_por_usuario = {}

# Ajuste as variáveis de ambiente conforme seu Render ou outro ambiente
openai.api_key = os.environ["OPENAI_API_KEY"]
INSTAGRAM_TOKEN = os.environ["INSTAGRAM_TOKEN"]
APP_SECRET = os.environ["INSTAGRAM_APP_SECRET"]

# Configuração para acessar Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
gc = gspread.authorize(credentials)
sheet = gc.open("webhook_instagram_logs").sheet1

def ler_lista_exclusao():
    """
    Lê um arquivo excluir_usuarios.txt (um por linha)
    e retorna uma lista de usernames a serem ignorados
    pelo robô.
    """
    try:
        with open("excluir_usuarios.txt", "r") as f:
            return [linha.strip().lower() for linha in f if linha.strip()]
    except FileNotFoundError:
        return []

def classificar_sentimento(texto):
    """
    Exemplo de chamada ao endpoint de chat do OpenAI na versão >=1.0.0
    Usando openai.ChatCompletion.create (nova sintaxe).
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "Classifique a seguinte mensagem como: positivo, neutro, negativo ou sensível."
                },
                {
                    "role": "user",
                    "content": texto
                }
            ],
            temperature=0.4,
            max_tokens=10
        )
        # A resposta vem em response.choices[0].message["content"]
        classificacao = response.choices[0].message["content"].strip().lower()
        return classificacao
    except Exception as e:
        print("Erro ao classificar sentimento:", e)
        return "neutro"

def gerar_resposta(texto, sentimento, tipo, interacoes):
    base = ""

    if "consulta" in texto.lower() or "atendimento" in texto.lower():
        base = (
            "Sou médico especialista em clínica médica (RQE 18790), com 13 anos de experiência "
            "e ex-professor de medicina. Ajudo pessoas que passaram por relacionamentos abusivos "
            "a se regularem emocionalmente e superarem sintomas físicos e psicológicos do trauma, "
            "como ansiedade, insônia, confusão mental e hipervigilância."
        )
    elif "não tenho dinheiro" in texto.lower() or "não posso pagar" in texto.lower():
        base = (
            "Entendo sua situação. Uma alternativa é o curso 'Quebrando as Algemas' com 50% de "
            "desconto usando o cupom **MQA50**. O acesso é por 1 ano e a renovação é automática "
            "(você pode cancelar na Hotmart a qualquer momento)."
        )
    elif sentimento == "sensível":
        base = (
            "Recebi sua mensagem com atenção. O que você sente é real e merece cuidado. "
            "Se quiser conversar, estou aqui."
        )
    elif sentimento == "negativo":
        base = (
            "Entendo que esse momento esteja difícil. Se precisar de uma direção, posso te orientar "
            "com cuidado e respeito."
        )
    elif sentimento == "positivo":
        base = (
            "Obrigado pela sua mensagem! Se quiser entender melhor como posso te ajudar, "
            "posso te explicar com calma."
        )
    elif sentimento == "neutro":
        base = (
            "Li sua mensagem. Me conta um pouco mais do que você está vivendo "
            "pra eu poder entender melhor."
        )

    # Se for direct e atingiu certo número de interações, adiciona CTA
    if tipo == "direct" and interacoes >= INTERACOES_ANTES_CTA:
        base += (
            " Se quiser conversar com alguém da minha equipe, clique aqui: "
            "https://api.whatsapp.com/send?phone=5527996677672&text=Olá!%20Gostaria%20de%20mais%"
            "20informações%20sobre%20as%20consultas%20com%20o%20Dr.%20Anderson%20Contaifer"
        )

    # Se for comentário, substituir link (só exemplo):
    if tipo == "comentario":
        base = base.replace("www.quebrandoasalgemas.com.br", "link da bio")

    # Limite de tamanho pro Instagram
    if tipo == "comentario":
        return base[:2200]
    else:
        return base[:1000]

def gerar_appsecret_proof(token, secret):
    """
    Gera o appsecret_proof para a Graph API,
    usando HMAC-SHA256(token + secret).
    """
    return hashlib.sha256((token + secret).encode('utf-8')).hexdigest()

def enviar_resposta_instagram(tipo, username, resposta, comment_id=None):
    """
    Envia resposta para comentário ou DM (direct) no Instagram,
    usando a Graph API com appsecret_proof.
    """
    try:
        proof = gerar_appsecret_proof(INSTAGRAM_TOKEN, APP_SECRET)

        if tipo == "comentario" and comment_id:
            url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
            r = requests.post(url, data={
                "message": resposta,
                "access_token": INSTAGRAM_TOKEN,
                "appsecret_proof": proof
            })
        elif tipo == "direct" and username:
            url = "https://graph.facebook.com/v19.0/me/messages"
            r = requests.post(url, json={
                "messaging_type": "RESPONSE",
                "recipient": {"id": username},
                "message": {"text": resposta},
                "access_token": INSTAGRAM_TOKEN,
                "appsecret_proof": proof
            })
        else:
            print("⚠️ Tipo inválido ou dados faltando.")
            return False

        if r.status_code != 200:
            print(f"❌ Falha ao enviar {tipo.upper()}: {r.status_code} - {r.text}")
            return False

        print(f"📤 {tipo.capitalize()} enviado com sucesso: {r.status_code}")
        return True

    except Exception as e:
        print("❌ Erro ao enviar resposta:", e)
        return False

def pode_responder(tipo, username):
    """
    Controla o limite de respostas por hora (tanto para direct quanto para comentário).
    """
    agora = time.time()
    historico = respostas_enviadas[tipo].get(username, [])
    # Limpa as tentativas antigas (mais de 1h)
    historico = [t for t in historico if agora - t < 3600]
    respostas_enviadas[tipo][username] = historico

    limite = MAX_COMENTARIOS_POR_HORA if tipo == "comentario" else MAX_DIRECTS_POR_HORA
    return len(historico) < limite

def registrar_resposta(tipo, username):
    respostas_enviadas[tipo].setdefault(username, []).append(time.time())

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    """
    Endpoint principal do webhook, que recebe eventos de
    comentários e mensagens do Instagram, e responde conforme
    a lógica.
    """
    if request.method == "GET":
        # Verificação do Webhook (setup inicial)
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        return "Unauthorized", 403

    if request.method == "POST":
        # Recebe um evento do Instagram
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
                    # Evento de comentário
                    tipo = "comentario"
                    value = entry["changes"][0]["value"]
                    username = value.get("from", {}).get("username", "").lower()
                    mensagem = value.get("text", "")
                    id_post = value.get("media", {}).get("id", "")
                    comment_id = value.get("id", "")
                elif "messaging" in entry:
                    # Evento de direct (mensagem)
                    tipo = "direct"
                    messaging = entry["messaging"][0]
                    mensagem = messaging.get("message", {}).get("text", "")
                    username = messaging.get("sender", {}).get("id", "")

            # Registra tudo no Google Sheets
            sheet.append_row([
                datetime.now().isoformat(),
                tipo,
                username,
                mensagem,
                id_post,
                "",  # espaço para eventual "emoji" ou outra info
                json.dumps(data)
            ])

            # Verifica se o usuário está na lista de exclusão
            if username in ler_lista_exclusao():
                print(f"🚫 Usuário ignorado (lista): {username}")
                return "Ignorado", 200

            # Checa se pode responder (limite por hora)
            if pode_responder(tipo, username):
                interacoes = interacoes_por_usuario.get(username, 0) + 1
                interacoes_por_usuario[username] = interacoes

                # Tenta classificar sentimento via OpenAI
                sentimento = classificar_sentimento(mensagem)
                resposta = gerar_resposta(mensagem, sentimento, tipo, interacoes)
                print(f"🤖 Resposta ({tipo}): {resposta}")

                # Dá um delay pequeno antes de enviar
                time.sleep(DELAY_ENTRE_RESPOSTAS)

                # Envia
                sucesso = enviar_resposta_instagram(
                    tipo,
                    username,
                    resposta,
                    comment_id if tipo == "comentario" else None
                )
                if sucesso:
                    registrar_resposta(tipo, username)
            else:
                print(f"⚠️ Limite de {tipo}s por hora para {username} atingido. Ignorando.")

        except Exception as e:
            print("❌ Erro geral:", str(e))

    return "OK", 200

if __name__ == "__main__":
    # Inicia a aplicação Flask
    app.run(host="0.0.0.0", port=8080)
